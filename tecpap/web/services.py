"""Services partagés par les routes de l'application web.

Les objets de longue durée (configuration, base, client des modèles de langue, routeur des
modèles, extracteur de commandes, conversion des médias, clients WhatsApp et email, générateur
de rapports, planificateur de sauvegardes, tâches de fond) sont construits une fois par
:func:`create_app` et rangés dans ``app.extensions["tecpap"]``. Les routes les obtiennent avec
:func:`get_services`, ce qui permet de créer plusieurs applications indépendantes (une par
test, par exemple).

Les objets liés à une connexion SQLite (magasin des conversations, base de connaissances,
assistant WhatsApp) sont construits à la demande sur la connexion du fil d'exécution courant :
une connexion n'est jamais partagée entre deux fils (voir :class:`tecpap.db.database.DatabaseManager`).

Au démarrage, quand ``RAG_ENABLED`` est vrai, la base de connaissances est indexée : seuls les
extraits nouveaux ou modifiés sont plongés, et tous les vecteurs sont recalculés si le calcul
des plongements a changé (``RAG_EMBEDDER``).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flask import current_app

from tecpap.channels.email_sender import EmailSender
from tecpap.channels.whatsapp import WhatsAppReceiver
from tecpap.chatbot.bot import WhatsAppAssistant
from tecpap.chatbot.conversation import ConversationStore, conversation_key
from tecpap.config import Settings
from tecpap.db.database import DatabaseManager
from tecpap.extraction.extractor import DataExtractor
from tecpap.llm.client import LLMClient
from tecpap.llm.router import ModelRouter
from tecpap.processing.multimodal import MediaProcessor
from tecpap.rag.answer import KnowledgeAnswerer
from tecpap.rag.retriever import IndexStats, KnowledgeBase, build_embedder
from tecpap.reporting.analytics import ReportGenerator
from tecpap.web.scheduler import BackupScheduler
from tecpap.web.worker import BackgroundWorker

logger = logging.getLogger(__name__)

EXTENSION_KEY = "tecpap"

_SENDER_LOCK_STRIPES = 64


class _ThreadObjects(threading.local):
    """Objets propres au fil d'exécution courant (base de connaissances et sa connexion)."""

    def __init__(self) -> None:
        self.knowledge: KnowledgeBase | None = None
        self.knowledge_connection: Any = None


@dataclass
class Services:
    """Conteneur des services de l'application."""

    settings: Settings
    db: DatabaseManager
    llm: LLMClient
    media: MediaProcessor
    whatsapp: WhatsAppReceiver
    email_sender: EmailSender
    report_generator: ReportGenerator
    backup_scheduler: BackupScheduler
    router: ModelRouter
    extractor: DataExtractor
    embedder: Any
    worker: BackgroundWorker
    _thread_objects: _ThreadObjects = field(default_factory=_ThreadObjects, repr=False)
    _sender_locks: tuple[threading.Lock, ...] = field(
        default_factory=lambda: tuple(threading.Lock() for _ in range(_SENDER_LOCK_STRIPES)), repr=False)
    _in_flight: set[str] = field(default_factory=set, repr=False)
    _in_flight_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def from_settings(cls, settings: Settings, llm: LLMClient | None = None) -> Services:
        """Construit tous les services ; la base est ouverte et son schéma initialisé.

        Args:
            settings: configuration de l'application.
            llm: client des modèles de langue ; par défaut construit depuis la configuration
                (les tests y substituent une doublure).
        """
        db = DatabaseManager(settings.database_path)
        db.connect()
        db.init_database()
        llm = llm if llm is not None else LLMClient.from_settings(settings)
        media = MediaProcessor(llm, settings)
        router = ModelRouter(settings)
        embedder = build_embedder(settings, llm)
        services = cls(
            settings=settings,
            db=db,
            llm=llm,
            media=media,
            whatsapp=WhatsAppReceiver(settings, llm=llm, media=media),
            email_sender=EmailSender(settings),
            report_generator=ReportGenerator(db, settings),
            backup_scheduler=BackupScheduler(
                db_path=settings.database_path,
                backup_dir=settings.backup_dir,
                interval_hours=settings.backup_interval_hours,
                keep_backups=settings.backup_keep,
            ),
            router=router,
            extractor=DataExtractor(db_manager=db, settings=settings, llm=llm, router=router, embedder=embedder),
            embedder=embedder,
            worker=BackgroundWorker(),
        )
        if settings.rag_enabled:
            services.index_knowledge_base()
        return services

    # ------------------------------------------------------------------ objets liés au fil courant

    def conversation_store(self) -> ConversationStore:
        """Magasin des conversations WhatsApp sur la connexion du fil courant."""
        return ConversationStore(self._connection())

    def knowledge_base(self) -> KnowledgeBase:
        """Base de connaissances sur la connexion du fil courant (gardée pour les appels suivants du fil)."""
        connection = self._connection()
        objects = self._thread_objects
        if objects.knowledge is None or objects.knowledge_connection is not connection:
            objects.knowledge = KnowledgeBase(connection, self.embedder, self.settings.knowledge_dir)
            objects.knowledge_connection = connection
        return objects.knowledge

    def knowledge_answerer(self) -> KnowledgeAnswerer | None:
        """Réponses fondées sur la base de connaissances, ou ``None`` quand ``RAG_ENABLED`` est faux."""
        if not self.settings.rag_enabled:
            return None
        return KnowledgeAnswerer(self.knowledge_base(), self.llm, self.router, self.settings)

    def assistant(self) -> WhatsAppAssistant:
        """Assistant WhatsApp sur la connexion du fil courant, avec l'extracteur et la base de connaissances."""
        connection = self._connection()
        return WhatsAppAssistant(
            connection, self.db, self.settings, llm=self.llm, router=self.router,
            extractor=self.extractor.assistant_extractor(), knowledge=self.knowledge_answerer(),
            store=ConversationStore(connection),
        )

    def _connection(self) -> Any:
        connection = self.db.connection
        if connection is None:
            # Gestionnaire fermé (restauration d'une sauvegarde, par exemple) : il est rouvert.
            self.db.connect()
            connection = self.db.connection
        if connection is None:
            raise RuntimeError("Base de données indisponible")
        return connection

    # ------------------------------------------------------------------ base de connaissances

    def index_knowledge_base(self, force: bool = False) -> IndexStats | None:
        """Met à jour l'index de la base de connaissances ; ``None`` si le dossier manque ou en cas d'échec.

        L'indexation est idempotente : un document inchangé n'est pas recalculé.
        """
        if not Path(self.settings.knowledge_dir).is_dir():
            logger.info("Base de connaissances non indexée : dossier %s absent", self.settings.knowledge_dir)
            return None
        try:
            stats = self.knowledge_base().index(force=force)
        except Exception:
            logger.exception("Indexation de la base de connaissances impossible")
            return None
        logger.info("Base de connaissances : %d extrait(s) indexé(s) (espace %s)", stats.total, stats.embedder)
        return stats

    # ------------------------------------------------------------------ messages WhatsApp

    def is_duplicate_message(self, message_sid: str | None) -> bool:
        """Vrai si le message Twilio ``message_sid`` a déjà été reçu (conversations) ou a créé une commande."""
        if not message_sid:
            return False
        return self.conversation_store().is_duplicate(message_sid) or self.db.is_message_processed(message_sid)

    def claim_message(self, message_sid: str | None) -> bool:
        """Réserve un message pendant son traitement ; faux s'il est déjà en cours de traitement.

        Twilio renvoie un message dont le webhook a tardé à répondre : la seconde livraison est
        écartée tant que la première n'est pas terminée. Un message sans identifiant est toujours accepté.
        """
        if not message_sid:
            return True
        with self._in_flight_lock:
            if message_sid in self._in_flight:
                return False
            self._in_flight.add(message_sid)
            return True

    def release_message(self, message_sid: str | None) -> None:
        """Libère un message réservé par :meth:`claim_message`."""
        if message_sid:
            with self._in_flight_lock:
                self._in_flight.discard(message_sid)

    def sender_lock(self, sender: str) -> threading.Lock:
        """Verrou d'un expéditeur : ses messages sont traités l'un après l'autre, quel que soit le fil."""
        try:
            key = conversation_key(sender)
        except ValueError:
            key = ""
        return self._sender_locks[hash(key) % _SENDER_LOCK_STRIPES]

    # ------------------------------------------------------------------ arrêt

    def close(self) -> None:
        """Arrête les tâches de fond et le planificateur, puis ferme toutes les connexions à la base."""
        self.worker.shutdown(wait=True)
        self.backup_scheduler.stop()
        self.db.close_all()


def get_services() -> Services:
    """Renvoie les services de l'application Flask courante."""
    return current_app.extensions[EXTENSION_KEY]
