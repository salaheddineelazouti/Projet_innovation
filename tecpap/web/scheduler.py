"""Planificateur des sauvegardes automatiques de la base, exécuté dans un fil d'arrière-plan.

Les sauvegardes ont lieu aux heures multiples de l'intervalle (0 h, 6 h, 12 h et 18 h pour un
intervalle de 6 heures ; toutes les heures pile pour un intervalle d'une heure), à l'heure du
serveur. Une heure ronde qui tombe moins de :data:`MIN_GAP_SECONDS` secondes après le calcul
(démarrage juste avant l'heure) est sautée au profit de la suivante. L'heure annoncée par
:meth:`BackupScheduler.get_status` est celle de la prochaine sauvegarde réelle. Seules les
``keep_backups`` plus récentes sont conservées.

Un seul planificateur tourne par base et par processus : sous le rechargeur de Werkzeug
(serveur de développement en mode débogage), le module est importé dans deux processus et
seul le processus enfant lance les sauvegardes (voir :func:`should_start_in_this_process`).
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Any

from tecpap.config import is_reloader_child_process
from tecpap.db.backup import create_backup, delete_old_backups

logger = logging.getLogger(__name__)

STARTUP_DELAY_SECONDS = 5
MIN_GAP_SECONDS = 300
"""Écart minimal entre le calcul de la prochaine sauvegarde et l'heure retenue, en secondes."""
RETRY_DELAY_SECONDS = 300

# Planificateurs actifs dans ce processus, par (base, dossier de sauvegarde).
_active_keys: set[tuple[str, str]] = set()
_active_lock = threading.Lock()


class SchedulerConfigError(ValueError):
    """Réglage du planificateur refusé (valeur non entière ou hors limites).

    Attributes:
        message: explication en français, destinée à l'utilisateur du tableau de bord.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def next_backup_time(now: datetime, interval_hours: int) -> datetime:
    """Renvoie la prochaine heure ronde multiple de ``interval_hours`` après ``now``."""
    if interval_hours < 1:
        raise ValueError("L'intervalle entre deux sauvegardes doit être d'au moins une heure")
    next_hour = ((now.hour // interval_hours) + 1) * interval_hours
    start_of_hour = now.replace(minute=0, second=0, microsecond=0)
    if next_hour >= 24:
        return start_of_hour.replace(hour=0) + timedelta(days=1)
    return start_of_hour.replace(hour=next_hour)


def scheduled_backup_time(now: datetime, interval_hours: int, min_gap_seconds: float = MIN_GAP_SECONDS) -> datetime:
    """Première heure ronde multiple de ``interval_hours`` située au moins ``min_gap_seconds`` après ``now``."""
    target = next_backup_time(now, interval_hours)
    while (target - now).total_seconds() < min_gap_seconds:
        target = next_backup_time(target, interval_hours)
    return target


def _whole_number(value: Any, message: str) -> int:
    """Convertit ``value`` (entier, décimal sans partie fractionnaire, chaîne) en entier.

    Raises:
        SchedulerConfigError: avec le message ``message`` quand la valeur n'est pas un nombre entier.
    """
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise SchedulerConfigError(message)
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        raise SchedulerConfigError(message) from None


def should_start_in_this_process(debug: bool) -> bool:
    """Vrai si le planificateur doit démarrer dans le processus courant.

    En mode débogage, le rechargeur de Werkzeug relance l'application dans un processus enfant
    marqué par ``WERKZEUG_RUN_MAIN=true`` ; le processus parent ne fait que surveiller les
    fichiers et ne doit pas lancer de sauvegardes.
    """
    return not debug or is_reloader_child_process()


class BackupScheduler:
    """Sauvegardes périodiques de la base dans un fil d'exécution démon.

    Args:
        db_path: base à sauvegarder.
        backup_dir: dossier des sauvegardes.
        interval_hours: intervalle entre deux sauvegardes, en heures.
        keep_backups: nombre de sauvegardes conservées.
    """

    def __init__(self, db_path: str, backup_dir: str, interval_hours: int = 6, keep_backups: int = 20):
        self.db_path = db_path
        self.backup_dir = backup_dir
        self.interval_hours = interval_hours
        self.keep_backups = keep_backups
        self.running = False
        self.thread: threading.Thread | None = None
        self.last_backup: datetime | None = None
        self.next_backup: datetime | None = None
        self._stop_event = threading.Event()

    @property
    def _key(self) -> tuple[str, str]:
        return os.path.abspath(self.db_path), os.path.abspath(self.backup_dir)

    def start(self) -> bool:
        """Démarre le fil de sauvegarde. Renvoie ``False`` s'il tourne déjà pour cette base."""
        if self.running:
            return False
        with _active_lock:
            if self._key in _active_keys:
                logger.info("Planificateur de sauvegarde déjà actif pour %s", self.db_path)
                return False
            _active_keys.add(self._key)

        self._stop_event.clear()
        self.running = True
        self.thread = threading.Thread(target=self._run_scheduler, name="backup-scheduler", daemon=True)
        self.thread.start()
        logger.info("Planificateur de sauvegarde démarré (toutes les %d h)", self.interval_hours)
        return True

    def stop(self) -> None:
        """Arrête le fil de sauvegarde (au plus tard à la fin de l'attente en cours)."""
        if not self.running:
            return
        self.running = False
        self._stop_event.set()
        with _active_lock:
            _active_keys.discard(self._key)

    def configure(self, interval_hours: Any = None, keep_backups: Any = None) -> None:
        """Change l'intervalle et le nombre de sauvegardes conservées (valeurs entières positives).

        Raises:
            SchedulerConfigError: valeur non entière ou hors limites (sous-classe de ``ValueError``).
        """
        interval = keep = None
        if interval_hours is not None:
            interval = _whole_number(interval_hours, "L'intervalle doit être un nombre entier d'heures")
            if not 1 <= interval <= 24:
                raise SchedulerConfigError("L'intervalle doit être compris entre 1 et 24 heures")
        if keep_backups is not None:
            keep = _whole_number(keep_backups, "Le nombre de sauvegardes conservées doit être un nombre entier")
            if keep < 1:
                raise SchedulerConfigError("Le nombre de sauvegardes conservées doit être au moins 1")
        if keep is not None:
            self.keep_backups = keep
        if interval is not None and interval != self.interval_hours:
            self.interval_hours = interval
            if self.running:
                # Réveille le fil : la prochaine sauvegarde est recalculée avec le nouvel intervalle.
                self.next_backup = scheduled_backup_time(datetime.now(), interval)
                self._stop_event.set()

    def run_backup(self) -> str | None:
        """Crée une sauvegarde compressée puis supprime les plus anciennes."""
        backup_path = create_backup(compress=True, db_path=self.db_path, backup_dir=self.backup_dir)
        if backup_path:
            self.last_backup = datetime.now()
            delete_old_backups(keep_count=self.keep_backups, backup_dir=self.backup_dir)
        return backup_path

    def _sleep(self, seconds: float) -> bool:
        """Attend ``seconds`` secondes ; vrai si l'attente a été interrompue (arrêt ou nouvel intervalle)."""
        if not self._stop_event.wait(seconds):
            return False
        if self.running:
            self._stop_event.clear()
        return True

    def _run_scheduler(self) -> None:
        self._sleep(STARTUP_DELAY_SECONDS)
        while self.running:
            try:
                now = datetime.now()
                self.next_backup = scheduled_backup_time(now, self.interval_hours)
                wait_seconds = (self.next_backup - now).total_seconds()
                logger.info("Prochaine sauvegarde à %s (dans %d h %02d min)", self.next_backup.strftime("%H:%M"),
                            int(wait_seconds // 3600), int((wait_seconds % 3600) // 60))
                if self._sleep(wait_seconds):
                    continue
                logger.info("Sauvegarde automatique programmée")
                self.run_backup()
            except Exception:
                logger.exception("Erreur du planificateur de sauvegarde")
                self._sleep(RETRY_DELAY_SECONDS)

    def get_status(self) -> dict[str, Any]:
        """État du planificateur (page des sauvegardes et API ``/api/backup/scheduler/status``).

        ``next_backup`` est l'heure à laquelle le fil lancera la prochaine sauvegarde.
        """
        return {
            "running": self.running,
            "interval_hours": self.interval_hours,
            "last_backup": self.last_backup.isoformat() if self.last_backup else None,
            "next_backup": self.next_backup.isoformat() if self.next_backup else None,
            "keep_backups": self.keep_backups,
        }
