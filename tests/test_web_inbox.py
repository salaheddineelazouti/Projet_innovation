"""File durable des messages WhatsApp reçus : enregistrement avant la réponse à Twilio, reprise, ordre, limite de débit.

Aucun appel réseau : l'envoi par l'API Twilio et la lecture des médias sont simulés.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

import pytest

from tecpap.chatbot.inbox import MAX_ATTEMPTS, STATUSES, WhatsAppInbox
from tecpap.chatbot.messages import render
from tecpap.config import Settings
from tecpap.security.events import KIND_RATE_LIMITED, recent_security_events
from tecpap.web import create_app
from tecpap.web.routes import webhooks
from tecpap.web.services import STALE_PROCESSING_SECONDS, STARTUP_GRACE_SECONDS, Services
from tecpap.web.worker import PeriodicTask

SENDER = "whatsapp:+212612345678"
OTHER = "whatsapp:+212698765432"


def message(body: str, sid: str, sender: str = SENDER, **extra) -> dict[str, str]:
    return {"From": sender, "To": "whatsapp:+14155238886", "Body": body, "MessageSid": sid,
            "NumMedia": "0", **extra}


def reply_text(response) -> str:
    return ElementTree.fromstring(response.get_data(as_text=True)).findtext("Message") or ""


def wait_until(condition, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition non remplie dans le délai"
        time.sleep(0.02)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def make_app(tmp_path):
    """Applications de test ; plusieurs applications peuvent partager le même dossier (redémarrage)."""
    apps = []

    def build(folder: str = "app", **overrides):
        app = create_app(Settings.for_tests(tmp_path / folder, **overrides))
        apps.append(app)
        return app

    yield build
    for app in apps:
        app.extensions["tecpap"].close()


@pytest.fixture
def sent(monkeypatch):
    """Réponses envoyées par l'API Twilio, pour toutes les applications du test."""
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr("tecpap.channels.whatsapp.WhatsAppReceiver.send_reply",
                        lambda self, number, text: messages.append((number, text)) or "SM-ENVOI")
    return messages


class TestInboxStore:
    """Table whatsapp_inbox."""

    @pytest.fixture
    def clock(self):
        return Clock()

    @pytest.fixture
    def inbox(self, clock):
        connection = sqlite3.connect(":memory:")
        yield WhatsAppInbox(connection, clock=clock)
        connection.close()

    def test_add_and_duplicate_sid(self, inbox):
        entry = inbox.add(message("bonjour", "SM1"))
        assert entry is not None and entry.status == "pending"
        assert inbox.add(message("bonjour", "SM1")) is None
        assert inbox.get(entry.id).payload["Body"] == "bonjour"

    def test_messages_without_sid_are_all_kept(self, inbox):
        assert inbox.add({"From": SENDER, "Body": "a"}) is not None
        assert inbox.add({"From": SENDER, "Body": "b"}) is not None
        assert inbox.counts()["pending"] == 2

    def test_claim_is_exclusive(self, inbox):
        entry = inbox.add(message("bonjour", "SM1"))
        claimed = inbox.claim(entry.id)
        assert claimed.status == "processing" and claimed.attempts == 1
        assert inbox.claim(entry.id) is None

    def test_done_and_failed(self, inbox):
        first, second = inbox.add(message("a", "SM1")), inbox.add(message("b", "SM2"))
        inbox.claim(first.id)
        inbox.mark_done(first.id, "Réponse non envoyée")
        inbox.claim(second.id)
        inbox.mark_failed(second.id, "x" * 2000)
        assert inbox.get(first.id).status == "done"
        assert inbox.get(first.id).last_error == "Réponse non envoyée"
        assert inbox.get(second.id).status == "failed"
        assert len(inbox.get(second.id).last_error) == 500
        assert inbox.counts() == {"pending": 0, "processing": 0, "done": 1, "failed": 1}
        assert set(inbox.counts()) == set(STATUSES)

    def test_interrupted_messages_are_requeued_then_abandoned(self, inbox, clock):
        entry = inbox.add(message("a", "SM1"))
        for attempt in range(1, MAX_ATTEMPTS + 1):
            assert inbox.claim(entry.id).attempts == attempt
            clock.now += timedelta(minutes=20)
            requeued, abandoned = inbox.requeue_interrupted(15 * 60)
            if attempt < MAX_ATTEMPTS:
                assert (requeued, abandoned) == (1, 0)
                assert inbox.get(entry.id).status == "pending"
        assert (requeued, abandoned) == (0, 1)
        assert inbox.get(entry.id).status == "failed"

    def test_recent_processing_is_left_alone(self, inbox, clock):
        entry = inbox.add(message("a", "SM1"))
        inbox.claim(entry.id)
        clock.now += timedelta(minutes=1)
        assert inbox.requeue_interrupted(15 * 60) == (0, 0)
        assert inbox.requeue_interrupted(0) == (1, 0)

    def test_pending_order_and_next_for_sender(self, inbox):
        first = inbox.add(message("1", "SM1"))
        inbox.add(message("autre", "SM2", sender=OTHER))
        inbox.add(message("2", "SM3"))
        assert [entry.message_sid for entry in inbox.pending()] == ["SM1", "SM2", "SM3"]
        assert inbox.next_pending_for(SENDER).id == first.id
        inbox.claim(first.id)
        assert inbox.next_pending_for(SENDER).message_sid == "SM3"

    def test_purge(self, inbox, clock):
        done, failed, pending = inbox.add(message("a", "SM1")), inbox.add(message("b", "SM2")), inbox.add(
            message("c", "SM3"))
        for entry, finish in ((done, inbox.mark_done), (failed, lambda entry_id: inbox.mark_failed(entry_id, "e"))):
            inbox.claim(entry.id)
            finish(entry.id)
        clock.now += timedelta(days=8)
        assert inbox.purge() == 1
        assert inbox.get(done.id) is None and inbox.get(failed.id) is not None
        clock.now += timedelta(days=30)
        assert inbox.purge() == 1
        assert inbox.get(pending.id) is not None


class TestDurableWebhook:
    """Un message accepté est enregistré avant la réponse à Twilio et n'est jamais perdu."""

    def test_saturated_worker_keeps_the_message_without_inline_processing(self, make_app, sent, monkeypatch):
        app = make_app(whatsapp_async_reply=True)
        services = app.extensions["tecpap"]
        calls = []
        monkeypatch.setattr(services.whatsapp, "process_incoming_message", lambda form: calls.append(form) or {})
        monkeypatch.setattr(services.worker, "submit", lambda *args, **kwargs: None)

        response = app.test_client().post("/webhook/whatsapp", data=message("bonjour", "SM1"))

        assert response.status_code == 200 and reply_text(response) == ""
        assert calls == []  # rien n'est traité dans le fil de la requête
        assert services.inbox().counts()["pending"] == 1

    def test_pending_message_is_resumed_after_a_restart(self, make_app, sent, monkeypatch):
        first = make_app("donnees", whatsapp_async_reply=True)
        monkeypatch.setattr(first.extensions["tecpap"].worker, "submit", lambda *args, **kwargs: None)
        assert first.test_client().post("/webhook/whatsapp", data=message("salam", "SM1")).status_code == 200
        first.extensions["tecpap"].close()  # arrêt du processus avant le traitement

        second = make_app("donnees", whatsapp_async_reply=True)
        services = second.extensions["tecpap"]
        assert webhooks.resume_pending_messages(services) == 1
        wait_until(lambda: services.inbox().counts()["done"] == 1)

        ((number, text),) = sent
        assert number == SENDER and text
        rows = services.db.connection.execute("SELECT direction FROM chat_messages ORDER BY id").fetchall()
        assert [row[0] for row in rows] == ["in", "out"]

    def test_interrupted_processing_is_resumed(self, make_app, sent):
        app = make_app(whatsapp_async_reply=True)
        services = app.extensions["tecpap"]
        inbox = services.inbox()
        entry = inbox.add(message("salam", "SM1"))
        inbox.claim(entry.id)  # pris en charge, puis processus arrêté

        assert webhooks.resume_pending_messages(services, 0.0) == 1
        wait_until(lambda: services.inbox().get(entry.id).status == "done")
        assert len(sent) == 1

    def test_redelivery_of_a_pending_message_is_ignored(self, make_app, sent, monkeypatch):
        app = make_app(whatsapp_async_reply=True)
        services = app.extensions["tecpap"]
        monkeypatch.setattr(services.worker, "submit", lambda *args, **kwargs: None)
        client = app.test_client()
        for _ in range(2):
            assert client.post("/webhook/whatsapp", data=message("salam", "SM1")).status_code == 200
        assert services.inbox().counts()["pending"] == 1

    def test_synchronous_reply_marks_the_message_done(self, make_app):
        app = make_app()
        services = app.extensions["tecpap"]
        response = app.test_client().post("/webhook/whatsapp", data=message("salam", "SM1"))
        assert reply_text(response)
        assert services.inbox().get(1).status == "done"

    def test_processing_error_marks_the_message_failed_and_answers_in_arabic(self, make_app, monkeypatch):
        app = make_app()
        services = app.extensions["tecpap"]

        def fail(form):
            raise RuntimeError("détail interne")

        monkeypatch.setattr(services.whatsapp, "process_incoming_message", fail)
        response = app.test_client().post("/webhook/whatsapp",
                                          data=message("السلام عليكم، أريد 3000 كيس ورقي بقاع مسطح", "SM1"))

        assert reply_text(response) == render("error", "ar")
        assert "détail interne" not in response.get_data(as_text=True)
        assert services.inbox().get(1).status == "failed"

    def test_messages_of_one_sender_are_processed_in_order(self, make_app, sent, monkeypatch):
        app = make_app(whatsapp_async_reply=True)
        services = app.extensions["tecpap"]
        release, handled = threading.Event(), []

        def handle(services_, form):
            if form["Body"] == "premier":
                release.wait(10)
            handled.append(form["Body"])
            return f"reçu : {form['Body']}"

        monkeypatch.setattr(webhooks, "handle_incoming_message", handle)
        client = app.test_client()
        client.post("/webhook/whatsapp", data=message("premier", "SM1"))
        client.post("/webhook/whatsapp", data=message("second", "SM2"))
        client.post("/webhook/whatsapp", data=message("autre client", "SM3", sender=OTHER))

        wait_until(lambda: "autre client" in handled)  # un autre expéditeur n'attend pas
        assert services.inbox().get(2).status == "pending"
        release.set()
        wait_until(lambda: services.inbox().counts()["done"] == 3)
        assert handled.index("premier") < handled.index("second")


class TestSenderTasks:
    """Une tâche de fond à la fois par expéditeur."""

    def test_second_task_of_a_sender_is_refused(self, make_app):
        services = make_app().extensions["tecpap"]
        release = threading.Event()
        assert services.submit_for_sender(SENDER, release.wait, 10) is True
        assert services.sender_has_task(SENDER)
        assert services.submit_for_sender(SENDER, lambda: None) is False
        assert services.submit_for_sender(OTHER, lambda: None) is True
        release.set()
        wait_until(lambda: not services.sender_has_task(SENDER))
        assert services.submit_for_sender(SENDER, lambda: None) is True

    def test_next_message_is_planned_even_when_the_task_fails(self, make_app):
        services = make_app().extensions["tecpap"]
        planned = threading.Event()

        def failing() -> None:
            raise RuntimeError("échec de la tâche")

        assert services.submit_for_sender(SENDER, failing, then=planned.set) is True
        wait_until(planned.is_set)
        wait_until(lambda: not services.sender_has_task(SENDER))

    def test_error_while_planning_the_next_message_is_logged(self, make_app, caplog):
        services = make_app().extensions["tecpap"]

        def broken() -> None:
            raise RuntimeError("planification impossible")

        assert services.submit_for_sender(SENDER, lambda: None, then=broken) is True
        wait_until(lambda: "Planification du message suivant" in caplog.text)
        assert not services.sender_has_task(SENDER)

    def test_refused_by_the_worker_frees_the_place(self, make_app, monkeypatch):
        services = make_app().extensions["tecpap"]
        monkeypatch.setattr(services.worker, "submit", lambda *args, **kwargs: None)
        assert services.submit_for_sender(SENDER, lambda: None) is False
        assert not services.sender_has_task(SENDER)


class TestRateLimitBeforeMedia:
    """Au-delà de la limite de débit, aucun média n'est téléchargé ni lu (appels payants)."""

    def _send_media(self, client, count: int) -> list:
        responses = []
        for index in range(count):
            data = message("", f"SM{index:032d}", MediaUrl0="https://api.twilio.com/Media/ME1",
                           MediaContentType0="audio/ogg")
            data["NumMedia"] = "1"
            responses.append(client.post("/webhook/whatsapp", data=data))
        return responses

    @pytest.mark.parametrize("chatbot_enabled", [True, False])
    def test_media_processing_stops_at_the_limit(self, make_app, monkeypatch, chatbot_enabled):
        app = make_app(chatbot_enabled=chatbot_enabled, whatsapp_rate_limit_per_minute=2)
        services = app.extensions["tecpap"]
        processed = []
        monkeypatch.setattr(services.whatsapp, "process_incoming_message",
                            lambda form: processed.append(form["MessageSid"]) or {"type": "audio", "transcription": ""})

        self._send_media(app.test_client(), 5)

        assert len(processed) == 2
        rows = services.db.connection.execute(
            "SELECT intent FROM chat_messages WHERE direction = 'in' ORDER BY id").fetchall()
        assert [row[0] for row in rows][2:] == ["rate_limited"] * 3

    def test_one_shot_mode_applies_the_limit_before_extraction(self, make_app, monkeypatch):
        app = make_app(chatbot_enabled=False, whatsapp_rate_limit_per_minute=2)
        services = app.extensions["tecpap"]
        extractions = []
        monkeypatch.setattr(services.extractor, "extract_text",
                            lambda text, **kwargs: extractions.append(text) or {"est_bon_commande": False})
        client = app.test_client()

        replies = [reply_text(client.post("/webhook/whatsapp", data=message(f"bonjour {index}", f"SM{index}")))
                   for index in range(4)]

        assert len(extractions) == 2
        assert replies[2] == render("rate_limited", "fr")
        assert replies[3] == ""
        (event,) = recent_security_events(services.db.connection, kind=KIND_RATE_LIMITED)
        assert event["channel"] == "whatsapp"


class TestMaintenance:
    """Maintenance périodique : reprise des messages et purge des journaux."""

    def test_periodic_task_repeats_and_survives_errors(self):
        calls = []

        def task():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("échec ponctuel")

        periodic = PeriodicTask(task, 0.01, name="test-periodique")
        assert periodic.start() is True
        assert periodic.start() is False
        wait_until(lambda: len(calls) >= 3)
        periodic.stop()
        assert not periodic.running

    def test_periodic_task_rejects_invalid_interval(self):
        with pytest.raises(ValueError):
            PeriodicTask(lambda: None, 0)

    def test_start_maintenance_resumes_at_startup_then_periodically(self, make_app, monkeypatch):
        services = make_app().extensions["tecpap"]
        monkeypatch.setattr("tecpap.web.services.MAINTENANCE_STARTUP_DELAY_SECONDS", 0.0)
        monkeypatch.setattr("tecpap.web.services.MAINTENANCE_INTERVAL_SECONDS", 0.01)
        calls = []
        assert services.start_maintenance(lambda svc, stale: calls.append(stale)) is True
        assert services.start_maintenance(lambda svc, stale: None) is False
        wait_until(lambda: len(calls) >= 2)
        services.close()
        assert 0 < calls[0] < STALE_PROCESSING_SECONDS  # durée écoulée depuis le démarrage, plus une marge
        assert calls[1] == STALE_PROCESSING_SECONDS

    def test_startup_resume_leaves_alone_a_message_the_process_is_handling(self, make_app, monkeypatch):
        services = make_app().extensions["tecpap"]
        inbox = services.inbox()
        interrupted = inbox.add(message("ancien", "SM-ANCIEN"))
        handled = inbox.add(message("en cours", "SM-EN-COURS", sender=OTHER))
        inbox.claim(interrupted.id)
        services.db.connection.execute("UPDATE whatsapp_inbox SET updated_at = '2000-01-01 00:00:00' WHERE id = ?",
                                       (interrupted.id,))
        services.db.connection.commit()
        inbox.claim(handled.id)  # pris en charge après le démarrage du processus
        monkeypatch.setattr(services.worker, "submit", lambda *args, **kwargs: None)

        webhooks.resume_pending_messages(services, 5.0 + STARTUP_GRACE_SECONDS)

        assert inbox.get(interrupted.id).status == "pending"
        assert inbox.get(handled.id).status == "processing"

    def test_run_maintenance_purges_old_security_events(self, make_app):
        services = make_app().extensions["tecpap"]
        connection = services.db.connection
        connection.execute("INSERT INTO security_events (created_at, channel, sender, kind, score, details) "
                           "VALUES ('2000-01-01 00:00:00', 'web', '', 'test', 0, '')")
        connection.commit()
        services.run_maintenance(webhooks.resume_pending_messages, stale_after_seconds=0)
        assert connection.execute("SELECT COUNT(*) FROM security_events").fetchone()[0] == 0

    def test_create_app_starts_maintenance_outside_tests(self, tmp_path, monkeypatch):
        started = []
        monkeypatch.setattr(Services, "start_maintenance", lambda self, resume: started.append(resume) or True)
        monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
        app = create_app(Settings.for_tests(tmp_path, env="development"))
        try:
            assert started == [webhooks.resume_pending_messages]
        finally:
            app.extensions["tecpap"].close()


class TestDashboard:
    """La page des conversations signale les messages en attente ou en échec."""

    def test_inbox_status_on_conversations_page(self, flask_client, services):
        inbox = services.inbox()
        inbox.add(message("a", "SM1"))
        failed = inbox.add(message("b", "SM2"))
        inbox.claim(failed.id)
        inbox.mark_failed(failed.id, "erreur")
        html = flask_client.get("/conversations").get_data(as_text=True)
        assert 'id="inboxStatus"' in html
        assert "1 en échec" in html

    def test_no_notice_when_inbox_is_empty(self, flask_client):
        assert 'id="inboxStatus"' not in flask_client.get("/conversations").get_data(as_text=True)
