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

Messages WhatsApp : chaque message signé est d'abord enregistré dans la file durable
(:mod:`tecpap.chatbot.inbox`). Un expéditeur a au plus :data:`MAX_TASKS_PER_SENDER` tâche de
fond à la fois (:meth:`Services.submit_for_sender`) : ses messages sont traités dans l'ordre, et
une rafale venue d'un seul numéro n'occupe pas toutes les places de la file des tâches. La
maintenance périodique (:meth:`Services.start_maintenance`) reprend les messages en attente et
supprime les anciennes lignes des journaux.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flask import current_app

from tecpap.channels.email_sender import EmailSender
from tecpap.channels.whatsapp import WhatsAppReceiver
from tecpap.chatbot.bot import WhatsAppAssistant
from tecpap.chatbot.conversation import ConversationStore, conversation_key
from tecpap.chatbot.inbox import WhatsAppInbox
from tecpap.chatbot.inbox import ensure_schema as ensure_inbox_schema
from tecpap.config import Settings
from tecpap.db.database import DatabaseManager
from tecpap.extraction.extractor import DataExtractor
from tecpap.llm.client import LLMClient
from tecpap.llm.router import ModelRouter
from tecpap.processing.multimodal import MediaProcessor
from tecpap.rag.answer import KnowledgeAnswerer
from tecpap.rag.retriever import IndexStats, KnowledgeBase, build_embedder
from tecpap.reporting.analytics import ReportGenerator
from tecpap.security.events import EventThrottle, purge_security_events
from tecpap.web.scheduler import BackupScheduler
from tecpap.web.worker import BackgroundWorker, PeriodicTask

logger = logging.getLogger(__name__)

EXTENSION_KEY = "tecpap"

MAX_TASKS_PER_SENDER = 1
"""Nombre maximal de tâches de fond en attente ou en cours pour un même expéditeur."""

MAINTENANCE_INTERVAL_SECONDS = 60.0
"""Intervalle de la maintenance périodique (reprise des messages WhatsApp en attente)."""

MAINTENANCE_STARTUP_DELAY_SECONDS = 5.0
"""Attente avant la première maintenance, qui reprend les messages interrompus par l'arrêt précédent."""

STALE_PROCESSING_SECONDS = 15 * 60
"""Durée au-delà de laquelle un message pris en charge et jamais terminé est considéré comme interrompu."""

STARTUP_GRACE_SECONDS = 1.0
"""Marge, en secondes, avant le démarrage du processus : la date des messages est enregistrée à la seconde près."""

PURGE_INTERVAL_SECONDS = 3600.0
"""Intervalle de la purge des journaux (événements de sécurité, messages WhatsApp traités)."""

Resumer = Callable[["Services", float], Any]
"""Reprise des messages en attente : ``resume(services, stale_after_seconds)``."""

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
    # Limite des événements « signature Twilio invalide » enregistrés (par adresse IP et au total).
    signature_event_throttle: EventThrottle = field(default_factory=EventThrottle, repr=False)
    maintenance: PeriodicTask | None = field(default=None, repr=False)
    _sender_tasks: dict[str, int] = field(default_factory=dict, repr=False)
    _sender_tasks_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last_purge: float | None = field(default=None, repr=False)

    @classmethod
    def from_settings(cls, settings: Settings, llm: LLMClient | None = None) -> Services:
        """Construit tous les services ; la base est ouverte et son schéma initialisé.

        Args:
            settings: configuration de l'application.
            llm: client des modèles de langue ; par défaut construit depuis la configuration
                (les tests y substituent une doublure).
        """
        db = DatabaseManager(settings.database_path)
        if not db.connect():
            raise RuntimeError(f"Base de données {settings.database_path} impossible à ouvrir : vérifiez le chemin "
                               "DATABASE_PATH et les droits d'écriture sur son dossier")
        db.init_database()
        ensure_inbox_schema(db.connection)
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

    def inbox(self) -> WhatsAppInbox:
        """File durable des messages WhatsApp reçus, sur la connexion du fil courant."""
        return WhatsAppInbox(self._connection())

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
        """Vrai si le message Twilio ``message_sid`` a déjà été reçu (conversations) ou a créé une commande.

        La file durable (:meth:`inbox`) complète ce contrôle : un message reçu mais pas encore
        traité y est déjà enregistré, et une seconde livraison n'y est pas ajoutée.
        """
        if not message_sid:
            return False
        return self.conversation_store().is_duplicate(message_sid) or self.db.is_message_processed(message_sid)

    def claim_message(self, message_sid: str | None) -> bool:
        """Réserve un identifiant de message dans ce processus ; faux s'il est déjà réservé.

        Réservation en mémoire, pour un traitement qui ne passe pas par la file durable (le
        webhook, lui, s'appuie sur :meth:`tecpap.chatbot.inbox.WhatsAppInbox.claim`, valable entre
        processus). Un message sans identifiant est toujours accepté.
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
        return self._sender_locks[hash(_sender_key(sender)) % _SENDER_LOCK_STRIPES]

    def submit_for_sender(self, sender: str, task: Callable[..., Any], *args: Any,
                          then: Callable[[], Any] | None = None) -> bool:
        """Confie ``task(*args)`` aux tâches de fond, sauf si l'expéditeur en a déjà :data:`MAX_TASKS_PER_SENDER`.

        Args:
            sender: expéditeur du message traité par la tâche.
            task: tâche à exécuter.
            *args: arguments de la tâche.
            then: fonction appelée dans le même fil après la tâche, une fois la place de
                l'expéditeur libérée (planification de son message suivant, par exemple).

        Returns:
            ``True`` si la tâche est planifiée ; ``False`` si l'expéditeur a déjà une tâche en
            attente ou en cours, ou si les tâches de fond sont saturées ou arrêtées.
        """
        key = _sender_key(sender)
        with self._sender_tasks_lock:
            if self._sender_tasks.get(key, 0) >= MAX_TASKS_PER_SENDER:
                return False
            self._sender_tasks[key] = self._sender_tasks.get(key, 0) + 1

        def run() -> None:
            try:
                task(*args)
            finally:
                self._task_finished(key)
                if then is not None:
                    try:
                        then()
                    except Exception:
                        logger.exception("Planification du message suivant de l'expéditeur impossible")

        if self.worker.submit(run) is None:
            self._task_finished(key)
            return False
        return True

    def sender_has_task(self, sender: str) -> bool:
        """Vrai quand une tâche de fond de cet expéditeur est en attente ou en cours."""
        with self._sender_tasks_lock:
            return self._sender_tasks.get(_sender_key(sender), 0) > 0

    def _task_finished(self, key: str) -> None:
        with self._sender_tasks_lock:
            remaining = self._sender_tasks.get(key, 0) - 1
            if remaining > 0:
                self._sender_tasks[key] = remaining
            else:
                self._sender_tasks.pop(key, None)

    # ------------------------------------------------------------------ maintenance périodique

    def start_maintenance(self, resume: Resumer) -> bool:
        """Démarre la maintenance périodique (une seule fois par processus et par application).

        La première exécution reprend les messages WhatsApp dont le traitement a été interrompu
        par l'arrêt précédent, c'est-à-dire pris en charge avant ce démarrage ; un message pris en
        charge depuis le démarrage est en cours de traitement et reste en place. Les exécutions
        suivantes, toutes les :data:`MAINTENANCE_INTERVAL_SECONDS` secondes, soumettent de nouveau
        les messages en attente et ceux dont le traitement est interrompu depuis
        :data:`STALE_PROCESSING_SECONDS` secondes.

        Args:
            resume: reprise des messages en attente (:func:`tecpap.web.routes.webhooks.resume_pending_messages`).
        """
        if self.maintenance is not None and self.maintenance.running:
            return False
        started = time.monotonic()
        state = {"startup": True}

        def task() -> None:
            if state["startup"]:
                stale_after = time.monotonic() - started + STARTUP_GRACE_SECONDS
            else:
                stale_after = STALE_PROCESSING_SECONDS
            state["startup"] = False
            self.run_maintenance(resume, stale_after_seconds=stale_after)

        self.maintenance = PeriodicTask(task, MAINTENANCE_INTERVAL_SECONDS,
                                        initial_delay=MAINTENANCE_STARTUP_DELAY_SECONDS, name="tecpap-maintenance")
        self.maintenance.start()
        logger.info("Maintenance périodique démarrée (toutes les %d s)", int(MAINTENANCE_INTERVAL_SECONDS))
        return True

    def run_maintenance(self, resume: Resumer, *, stale_after_seconds: float = STALE_PROCESSING_SECONDS) -> None:
        """Reprend les messages WhatsApp en attente puis, au plus une fois par heure, purge les journaux."""
        self.db.connect()
        resume(self, stale_after_seconds)
        now = time.monotonic()
        if self._last_purge is None or now - self._last_purge >= PURGE_INTERVAL_SECONDS:
            self._last_purge = now
            self.purge_logs()

    def purge_logs(self) -> None:
        """Supprime les anciens événements de sécurité et les messages WhatsApp traités depuis longtemps."""
        connection = self._connection()
        purge_security_events(connection)
        try:
            WhatsAppInbox(connection).purge()
        except Exception:
            logger.exception("Purge de la file des messages WhatsApp impossible")

    # ------------------------------------------------------------------ arrêt

    def close(self) -> None:
        """Arrête la maintenance, les tâches de fond et le planificateur, puis ferme toutes les connexions à la base."""
        if self.maintenance is not None:
            self.maintenance.stop()
        self.worker.shutdown(wait=True)
        self.backup_scheduler.stop()
        self.db.close_all()


def _sender_key(sender: str | None) -> str:
    """Clé d'un expéditeur (numéro international), ou chaîne vide pour un expéditeur illisible."""
    try:
        return conversation_key(sender)
    except ValueError:
        return ""


def get_services() -> Services:
    """Renvoie les services de l'application Flask courante."""
    return current_app.extensions[EXTENSION_KEY]
