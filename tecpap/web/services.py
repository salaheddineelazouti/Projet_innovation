"""Services partagés par les routes de l'application web.

Les objets de longue durée (configuration, base, clients WhatsApp et email, générateur de
rapports, planificateur de sauvegardes) sont construits une fois par :func:`create_app` et
rangés dans ``app.extensions["tecpap"]``. Les routes les obtiennent avec :func:`get_services`,
ce qui permet de créer plusieurs applications indépendantes (une par test, par exemple).
"""

from __future__ import annotations

from dataclasses import dataclass

from flask import current_app

from tecpap.channels.email_sender import EmailSender
from tecpap.channels.whatsapp import WhatsAppReceiver
from tecpap.config import Settings
from tecpap.db.database import DatabaseManager
from tecpap.reporting.analytics import ReportGenerator
from tecpap.web.scheduler import BackupScheduler

EXTENSION_KEY = "tecpap"


@dataclass
class Services:
    """Conteneur des services de l'application."""

    settings: Settings
    db: DatabaseManager
    whatsapp: WhatsAppReceiver
    email_sender: EmailSender
    report_generator: ReportGenerator
    backup_scheduler: BackupScheduler

    @classmethod
    def from_settings(cls, settings: Settings) -> Services:
        """Construit tous les services ; la base est ouverte et son schéma initialisé."""
        db = DatabaseManager(settings.database_path)
        db.connect()
        db.init_database()
        return cls(
            settings=settings,
            db=db,
            whatsapp=WhatsAppReceiver(settings),
            email_sender=EmailSender(settings),
            report_generator=ReportGenerator(db, settings),
            backup_scheduler=BackupScheduler(
                db_path=settings.database_path,
                backup_dir=settings.backup_dir,
                interval_hours=settings.backup_interval_hours,
                keep_backups=settings.backup_keep,
            ),
        )

    def close(self) -> None:
        """Arrête le planificateur et ferme la base."""
        self.backup_scheduler.stop()
        self.db.disconnect()


def get_services() -> Services:
    """Renvoie les services de l'application Flask courante."""
    return current_app.extensions[EXTENSION_KEY]
