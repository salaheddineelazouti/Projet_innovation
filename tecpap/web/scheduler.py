"""Planificateur des sauvegardes automatiques de la base, exécuté dans un fil d'arrière-plan.

Les sauvegardes ont lieu aux heures multiples de l'intervalle (0 h, 6 h, 12 h et 18 h pour un
intervalle de 6 heures), avec au moins une heure entre deux sauvegardes ; seules les
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

from tecpap.db.backup import create_backup, delete_old_backups

logger = logging.getLogger(__name__)

STARTUP_DELAY_SECONDS = 5
MIN_WAIT_SECONDS = 3600
RETRY_DELAY_SECONDS = 300

# Planificateurs actifs dans ce processus, par (base, dossier de sauvegarde).
_active_keys: set[tuple[str, str]] = set()
_active_lock = threading.Lock()


def next_backup_time(now: datetime, interval_hours: int) -> datetime:
    """Renvoie la prochaine heure ronde multiple de ``interval_hours`` après ``now``."""
    if interval_hours < 1:
        raise ValueError("L'intervalle entre deux sauvegardes doit être d'au moins une heure")
    next_hour = ((now.hour // interval_hours) + 1) * interval_hours
    start_of_hour = now.replace(minute=0, second=0, microsecond=0)
    if next_hour >= 24:
        return start_of_hour.replace(hour=0) + timedelta(days=1)
    return start_of_hour.replace(hour=next_hour)


def should_start_in_this_process(debug: bool) -> bool:
    """Vrai si le planificateur doit démarrer dans le processus courant.

    En mode débogage, le rechargeur de Werkzeug relance l'application dans un processus enfant
    marqué par ``WERKZEUG_RUN_MAIN=true`` ; le processus parent ne fait que surveiller les
    fichiers et ne doit pas lancer de sauvegardes.
    """
    return not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true"


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
        """Change l'intervalle et le nombre de sauvegardes conservées (valeurs entières positives)."""
        if interval_hours is not None:
            interval = int(interval_hours)
            if not 1 <= interval <= 24:
                raise ValueError("L'intervalle doit être compris entre 1 et 24 heures")
            self.interval_hours = interval
        if keep_backups is not None:
            keep = int(keep_backups)
            if keep < 1:
                raise ValueError("Le nombre de sauvegardes conservées doit être au moins 1")
            self.keep_backups = keep

    def run_backup(self) -> str | None:
        """Crée une sauvegarde compressée puis supprime les plus anciennes."""
        backup_path = create_backup(compress=True, db_path=self.db_path, backup_dir=self.backup_dir)
        if backup_path:
            self.last_backup = datetime.now()
            delete_old_backups(keep_count=self.keep_backups, backup_dir=self.backup_dir)
        return backup_path

    def _run_scheduler(self) -> None:
        if self._stop_event.wait(STARTUP_DELAY_SECONDS):
            return
        while self.running:
            try:
                self.next_backup = next_backup_time(datetime.now(), self.interval_hours)
                wait_seconds = max((self.next_backup - datetime.now()).total_seconds(), MIN_WAIT_SECONDS)
                logger.info("Prochaine sauvegarde dans %d h %02d min",
                            int(wait_seconds // 3600), int((wait_seconds % 3600) // 60))
                if self._stop_event.wait(wait_seconds):
                    break
                logger.info("Sauvegarde automatique programmée")
                self.run_backup()
            except Exception:
                logger.exception("Erreur du planificateur de sauvegarde")
                if self._stop_event.wait(RETRY_DELAY_SECONDS):
                    break

    def get_status(self) -> dict[str, Any]:
        """État du planificateur, affiché sur la page des sauvegardes."""
        return {
            "running": self.running,
            "interval_hours": self.interval_hours,
            "last_backup": self.last_backup.isoformat() if self.last_backup else None,
            "next_backup": self.next_backup.isoformat() if self.next_backup else None,
            "keep_backups": self.keep_backups,
        }
