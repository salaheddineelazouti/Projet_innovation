"""Tâches de fond de l'application web : traitement différé des messages WhatsApp.

Twilio attend la réponse du webhook au plus 15 secondes. Une note vocale à transcrire, une image
à lire ou plusieurs appels au modèle de langue peuvent dépasser ce délai : avec
``WHATSAPP_ASYNC_REPLY=true``, le webhook répond aussitôt par un TwiML vide (format XML de
Twilio, ici sans message) et le message est traité par :class:`BackgroundWorker` ; la réponse au
client part ensuite par l'API REST de Twilio.

Le nombre de fils d'exécution est borné (deux par défaut), de même que le nombre de tâches en
attente : au-delà, :meth:`BackgroundWorker.submit` refuse la tâche et l'appelant la traite
lui-même. Une exception levée par une tâche est consignée dans le journal, jamais propagée.
Les fils sont arrêtés à la fermeture de l'application (:meth:`tecpap.web.services.Services.close`)
ou, à défaut, à l'arrêt de l'interpréteur Python (module :mod:`atexit`).
"""

from __future__ import annotations

import atexit
import logging
import threading
import weakref
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_MAX_PENDING", "DEFAULT_MAX_WORKERS", "BackgroundWorker"]

DEFAULT_MAX_WORKERS = 2
"""Nombre de fils d'exécution des tâches de fond."""

DEFAULT_MAX_PENDING = 100
"""Nombre maximal de tâches acceptées et pas encore terminées."""

_WORKERS: weakref.WeakSet[BackgroundWorker] = weakref.WeakSet()


class BackgroundWorker:
    """Exécuteur borné de tâches de fond (un :class:`~concurrent.futures.ThreadPoolExecutor`).

    Les fils ne sont créés qu'à la première tâche.

    Args:
        max_workers: nombre de fils d'exécution.
        max_pending: nombre maximal de tâches en attente ou en cours.
        name: préfixe du nom des fils (visible dans le journal).
    """

    def __init__(self, max_workers: int = DEFAULT_MAX_WORKERS, max_pending: int = DEFAULT_MAX_PENDING,
                 name: str = "tecpap-tache"):
        if max_workers < 1 or max_pending < 1:
            raise ValueError("max_workers et max_pending doivent être strictement positifs")
        self.max_workers = max_workers
        self.max_pending = max_pending
        self.name = name
        self._executor: ThreadPoolExecutor | None = None
        self._slots = threading.BoundedSemaphore(max_pending)
        self._lock = threading.Lock()
        self._closed = False
        _WORKERS.add(self)

    @property
    def closed(self) -> bool:
        """Vrai après :meth:`shutdown` : aucune tâche n'est plus acceptée."""
        return self._closed

    def submit(self, task: Callable[..., Any], *args: Any, **kwargs: Any) -> Future[Any] | None:
        """Planifie ``task(*args, **kwargs)`` ; renvoie ``None`` si la tâche est refusée.

        La tâche est refusée quand l'exécuteur est arrêté ou que :attr:`max_pending` tâches
        attendent déjà : l'appelant la traite alors lui-même.
        """
        if not self._slots.acquire(blocking=False):
            logger.warning("Tâches de fond saturées (%d en attente) : tâche refusée", self.max_pending)
            return None
        with self._lock:
            if self._closed:
                self._slots.release()
                logger.warning("Tâches de fond arrêtées : tâche refusée")
                return None
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix=self.name)
            try:
                return self._executor.submit(self._run, task, args, kwargs)
            except RuntimeError:
                # Interpréteur en cours d'arrêt : l'exécuteur n'accepte plus de tâche.
                self._slots.release()
                logger.warning("Tâche de fond refusée : arrêt en cours")
                return None

    def _run(self, task: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        try:
            return task(*args, **kwargs)
        except Exception:
            logger.exception("Échec d'une tâche de fond")
            return None
        finally:
            self._slots.release()

    def shutdown(self, wait: bool = True) -> None:
        """Refuse les nouvelles tâches et arrête les fils, après les tâches en cours si ``wait``."""
        with self._lock:
            self._closed = True
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=wait)
            logger.info("Tâches de fond arrêtées")


@atexit.register
def _shutdown_all() -> None:
    """Arrête, à la sortie de l'interpréteur, les exécuteurs encore ouverts."""
    for worker in list(_WORKERS):
        worker.shutdown(wait=False)
