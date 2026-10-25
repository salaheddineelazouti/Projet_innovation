"""Validation, rejet et modification des commandes : transitions de statut, notifications, auteur, sonde de santé.

Aucun message n'est réellement envoyé : l'envoi des emails et des messages WhatsApp est simulé.
"""

from __future__ import annotations

import logging
import sqlite3
import threading

import pytest

from tecpap.chatbot.messages import render
from tecpap.processing.order_processor import OrderProcessor, ProcessingAlreadyRunning
from tecpap.web.routes.api import (
    DECIDED_ORDER_LOCKED_FIELDS,
    NOTIFICATION_FAILED,
    NOTIFICATION_NOT_APPLICABLE,
    NOTIFICATION_SENT,
    PROCESSING_RUNNING_MESSAGE,
)

IMPOSTOR = "whatsapp:+212699999999"
CLIENT_PHONE = "+212612345678"


@pytest.fixture
def mails(services, monkeypatch):
    """Emails de notification enregistrés (aucun envoi réel)."""
    sent: list[tuple[str, int | None, str]] = []

    def validation(order):
        sent.append(("validation", order["id"], ""))
        return True

    def rejection(order, reason=""):
        sent.append(("rejet", order["id"], reason))
        return True

    monkeypatch.setattr(services.email_sender, "send_validation_email", validation)
    monkeypatch.setattr(services.email_sender, "send_rejection_email", rejection)
    return sent


@pytest.fixture
def whatsapp(services, monkeypatch):
    """Messages WhatsApp de notification enregistrés (aucun envoi réel)."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(services.whatsapp, "send_reply", lambda number, message: sent.append((number, message)) or "SM1")
    return sent


def user_log_rows(services, order_id):
    cursor = services.db.connection.execute(
        "SELECT action, details, user_id FROM logs WHERE table_name = 'commandes' AND record_id = ? ORDER BY id",
        (order_id,),
    )
    return [tuple(row) for row in cursor.fetchall()]


class TestStatusTransitions:
    """Seule une commande en attente peut être validée ou rejetée ; la seconde décision reçoit 409."""

    def test_validating_twice_notifies_once(self, flask_client, services, sample_order_data, mails):
        order_id = services.db.create_order(sample_order_data)
        assert flask_client.post(f"/api/orders/{order_id}/validate", json={}).status_code == 200

        second = flask_client.post(f"/api/orders/{order_id}/validate", json={})

        assert second.status_code == 409
        body = second.get_json()
        assert body["success"] is False and body["status"] == "validee"
        assert "validée" in body["error"]
        assert mails == [("validation", order_id, "")]

    def test_rejecting_a_validated_order_changes_nothing(self, flask_client, services, sample_order_data, mails):
        order_id = services.db.create_order(sample_order_data)
        flask_client.post(f"/api/orders/{order_id}/validate", json={})

        response = flask_client.post(f"/api/orders/{order_id}/reject", json={"reason": "Stock insuffisant"})

        assert response.status_code == 409
        order = services.db.get_order(order_id)
        assert order["statut"] == "validee"
        assert not order["motif_rejet"]
        assert [kind for kind, *_ in mails] == ["validation"]

    def test_validating_a_rejected_order_changes_nothing(self, flask_client, services, sample_order_data, mails):
        order_id = services.db.create_order(sample_order_data)
        flask_client.post(f"/api/orders/{order_id}/reject", json={"reason": "Doublon"})

        response = flask_client.post(f"/api/orders/{order_id}/validate", json={"validated_by": "Karim"})

        assert response.status_code == 409
        assert response.get_json()["status"] == "rejetee"
        order = services.db.get_order(order_id)
        assert order["statut"] == "rejetee" and order["motif_rejet"] == "Doublon"
        assert not order["validated_by"]
        assert [kind for kind, *_ in mails] == ["rejet"]

    @pytest.mark.parametrize("action, body", [("validate", {}), ("reject", {"reason": "Test"})])
    def test_deleted_order_cannot_be_decided(self, flask_client, services, sample_order_data, mails, action, body):
        order_id = services.db.create_order(sample_order_data)
        services.db.delete_order(order_id)

        response = flask_client.post(f"/api/orders/{order_id}/{action}", json=body)

        assert response.status_code == 409
        assert services.db.get_order(order_id)["statut"] == "supprimee"
        assert mails == []

    def test_simultaneous_decisions_give_one_winner(self, flask_app, services, sample_order_data, mails):
        order_id = services.db.create_order(sample_order_data)
        barrier = threading.Barrier(6)
        statuses: list[int] = []

        def decide(path: str, payload: dict) -> None:
            with flask_app.test_client() as client:
                barrier.wait(timeout=10)
                statuses.append(client.post(f"/api/orders/{order_id}/{path}", json=payload).status_code)

        threads = [threading.Thread(target=decide, args=("validate", {})) for _ in range(3)]
        threads += [threading.Thread(target=decide, args=("reject", {"reason": "Test"})) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert sorted(statuses) == [200, 409, 409, 409, 409, 409]
        assert len(mails) == 1
        assert services.db.get_order(order_id)["statut"] in ("validee", "rejetee")


class TestPhoneBinding:
    """Valider le contenu d'une commande ne prouve pas l'identité de l'expéditeur : aucun numéro n'est rattaché."""

    def test_validation_does_not_attach_the_sender_phone(self, flask_client, services, sample_order_data, whatsapp):
        email_order = services.db.create_order(dict(sample_order_data, entreprise_cliente="Boulangerie Nour"))
        services.db.update_order_status(email_order, "validee", "Karim")
        impostor_order = services.db.create_order({
            "entreprise_cliente": "Boulangerie Nour", "type_produit": "Sachets fond plat", "quantite": 3000,
            "source": "whatsapp", "whatsapp_from": IMPOSTOR, "email_id": "whatsapp_SM-IMPOSTOR",
        })
        client_id = services.db.get_order(impostor_order)["client_id"]
        assert client_id == services.db.get_order(email_order)["client_id"]

        response = flask_client.post(f"/api/orders/{impostor_order}/validate", json={})

        assert response.status_code == 200
        row = services.db.connection.execute("SELECT telephone FROM clients WHERE id = ?", (client_id,)).fetchone()
        assert not row["telephone"]

    def test_rejection_does_not_attach_the_phone_either(self, flask_client, services, sample_whatsapp_order, whatsapp):
        order_id = services.db.create_order(sample_whatsapp_order)
        client_id = services.db.get_order(order_id)["client_id"]
        before = services.db.connection.execute("SELECT telephone FROM clients WHERE id = ?", (client_id,)).fetchone()

        flask_client.post(f"/api/orders/{order_id}/reject", json={"reason": "Test"})

        after = services.db.connection.execute("SELECT telephone FROM clients WHERE id = ?", (client_id,)).fetchone()
        assert tuple(after) == tuple(before)


class TestNotifications:
    """Résultat de l'envoi au client : état par canal, avertissement, trace dans les logs, renvoi."""

    def test_both_channels_sent(self, flask_client, services, sample_whatsapp_order, mails, whatsapp):
        order_id = services.db.create_order(dict(sample_whatsapp_order, email_from="client@test.com"))

        data = flask_client.post(f"/api/orders/{order_id}/validate", json={}).get_json()

        assert data["notifications"] == {"email": NOTIFICATION_SENT, "whatsapp": NOTIFICATION_SENT}
        assert data["email_sent"] is True and data["whatsapp_sent"] is True
        assert data["warning"] is None
        assert [number for number, _ in whatsapp] == [CLIENT_PHONE]

    def test_failed_email_is_reported(self, flask_client, services, sample_order_data, monkeypatch, caplog):
        monkeypatch.setattr(services.email_sender, "send_validation_email", lambda order: False)
        order_id = services.db.create_order(sample_order_data)

        with caplog.at_level(logging.WARNING, logger="tecpap.web.routes.api"):
            response = flask_client.post(f"/api/orders/{order_id}/validate", json={})

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["notifications"] == {"email": NOTIFICATION_FAILED, "whatsapp": NOTIFICATION_NOT_APPLICABLE}
        assert "n'a pas été prévenu par email" in data["warning"]
        assert "Notification incomplète" in caplog.text

    def test_email_exception_is_reported_without_its_text(self, flask_client, services, sample_order_data, monkeypatch):
        def broken(order):
            raise RuntimeError("mot de passe SMTP invalide : secret-smtp")

        monkeypatch.setattr(services.email_sender, "send_validation_email", broken)
        order_id = services.db.create_order(sample_order_data)

        response = flask_client.post(f"/api/orders/{order_id}/validate", json={})

        assert response.status_code == 200
        assert response.get_json()["notifications"]["email"] == NOTIFICATION_FAILED
        assert "secret-smtp" not in response.get_data(as_text=True)

    def test_failed_whatsapp_is_reported(self, flask_client, services, sample_whatsapp_order, monkeypatch):
        def refused(number, message):
            raise RuntimeError("Hors de la fenêtre de 24 heures")

        monkeypatch.setattr(services.whatsapp, "send_reply", refused)
        order_id = services.db.create_order(sample_whatsapp_order)

        data = flask_client.post(f"/api/orders/{order_id}/validate", json={}).get_json()

        assert data["notifications"]["whatsapp"] == NOTIFICATION_FAILED
        assert data["whatsapp_sent"] is False
        assert "WhatsApp" in data["warning"]

    def test_order_without_any_channel_gets_a_warning(self, flask_client, services, sample_order_data):
        order_id = services.db.create_order(dict(sample_order_data, email_from="", email_id="sans-adresse"))

        data = flask_client.post(f"/api/orders/{order_id}/validate", json={}).get_json()

        assert data["notifications"] == {"email": NOTIFICATION_NOT_APPLICABLE, "whatsapp": NOTIFICATION_NOT_APPLICABLE}
        assert "ni adresse email ni numéro WhatsApp" in data["warning"]

    def test_result_is_written_to_the_logs(self, flask_client, services, sample_order_data, mails):
        order_id = services.db.create_order(sample_order_data)

        flask_client.post(f"/api/orders/{order_id}/validate", json={})

        details = [row[1] for row in user_log_rows(services, order_id) if row[0] == "NOTIFY"]
        assert details == ["Notification order_validated: email=sent, whatsapp=not_applicable"]

    def test_whatsapp_message_uses_the_conversation_language(self, flask_client, services, sample_whatsapp_order, whatsapp):
        conversation = services.conversation_store().get_or_create(CLIENT_PHONE)
        conversation.language = "darija"
        services.conversation_store().save(conversation)
        order_id = services.db.create_order(sample_whatsapp_order)

        flask_client.post(f"/api/orders/{order_id}/validate", json={})

        (_, message), = whatsapp
        reference = f"CMD-{order_id}"
        assert reference in message
        assert message.startswith(render("order_validated", "darija", reference=reference, lines="").split(reference)[0])
        stored = services.conversation_store().recent_messages(conversation.id, 5)
        assert stored[-1]["direction"] == "out" and stored[-1]["body"] == message

    def test_rejection_reason_reaches_the_email(self, flask_client, services, sample_order_data, mails):
        order_id = services.db.create_order(sample_order_data)

        flask_client.post(f"/api/orders/{order_id}/reject", json={"reason": "  Stock insuffisant  "})

        assert mails == [("rejet", order_id, "Stock insuffisant")]


class TestResendNotification:
    """Route de renvoi de la notification d'une décision."""

    def test_resend_for_a_validated_order(self, flask_client, services, sample_order_data, mails):
        order_id = services.db.create_order(sample_order_data)
        flask_client.post(f"/api/orders/{order_id}/validate", json={})

        response = flask_client.post(f"/api/orders/{order_id}/notify")

        assert response.status_code == 200
        assert response.get_json()["notifications"]["email"] == NOTIFICATION_SENT
        assert [kind for kind, *_ in mails] == ["validation", "validation"]

    def test_resend_for_a_rejected_order_carries_the_reason(self, flask_client, services, sample_order_data, mails):
        order_id = services.db.create_order(sample_order_data)
        flask_client.post(f"/api/orders/{order_id}/reject", json={"reason": "Doublon"})

        flask_client.post(f"/api/orders/{order_id}/notify")

        assert mails == [("rejet", order_id, "Doublon"), ("rejet", order_id, "Doublon")]

    def test_pending_order_has_no_decision_to_announce(self, flask_client, services, sample_order_data, mails):
        order_id = services.db.create_order(sample_order_data)

        response = flask_client.post(f"/api/orders/{order_id}/notify")

        assert response.status_code == 409
        assert mails == []

    def test_unknown_order(self, flask_client):
        assert flask_client.post("/api/orders/9999/notify").status_code == 404


class TestEditingDecidedOrders:
    """Les champs annoncés au client ne changent plus après la décision ; les champs de livraison restent modifiables."""

    @pytest.fixture
    def validated(self, flask_client, services, sample_order_data, mails):
        order_id = services.db.create_order(sample_order_data)
        flask_client.post(f"/api/orders/{order_id}/validate", json={})
        return order_id

    def test_quantity_is_locked(self, flask_client, services, validated):
        response = flask_client.post(f"/api/orders/{validated}/update", json={"quantite": 9999})

        assert response.status_code == 409
        body = response.get_json()
        assert body["rejected_fields"] == ["quantite"]
        assert body["status"] == "validee"
        assert services.db.get_order(validated)["quantite"] == 5000

    def test_several_locked_fields_are_listed(self, flask_client, validated):
        response = flask_client.post(f"/api/orders/{validated}/update",
                                     json={"quantite": 1, "prix_unitaire": 9.5, "code_article": "KR-80-90"})
        assert response.status_code == 409
        assert response.get_json()["rejected_fields"] == ["prix_unitaire", "quantite"]

    def test_unchanged_value_is_accepted(self, flask_client, services, validated):
        response = flask_client.post(f"/api/orders/{validated}/update", json={"quantite": 5000, "code_article": "KR-80-90"})
        assert response.status_code == 200
        assert services.db.get_order(validated)["code_article"] == "KR-80-90"

    def test_delivery_fields_stay_editable(self, flask_client, services, validated):
        response = flask_client.post(f"/api/orders/{validated}/update", json={"quantite_livree": 2000})
        assert response.status_code == 200
        assert services.db.get_order(validated)["reste_a_livrer"] == 3000

    def test_sage_fields_stay_editable(self, flask_client, services, validated):
        response = flask_client.put(f"/api/orders/{validated}/sage-fields", json={"code_article": "KR-70-100"})
        assert response.status_code == 200
        assert services.db.get_order(validated)["code_article"] == "KR-70-100"

    def test_pending_order_is_fully_editable(self, flask_client, services, sample_order_data):
        order_id = services.db.create_order(sample_order_data)
        assert flask_client.post(f"/api/orders/{order_id}/update", json={"quantite": 6000}).status_code == 200
        assert services.db.get_order(order_id)["quantite"] == 6000

    def test_rejected_order_is_locked_too(self, flask_client, services, sample_order_data, mails):
        order_id = services.db.create_order(sample_order_data)
        flask_client.post(f"/api/orders/{order_id}/reject", json={"reason": "Doublon"})
        assert flask_client.post(f"/api/orders/{order_id}/update", json={"quantite": 1}).status_code == 409

    def test_locked_fields_are_the_ones_announced_to_the_client(self):
        assert {"quantite", "produit_id", "prix_unitaire", "prix_total", "date_livraison"} <= set(DECIDED_ORDER_LOCKED_FIELDS)
        assert "quantite_livree" not in DECIDED_ORDER_LOCKED_FIELDS and "code_article" not in DECIDED_ORDER_LOCKED_FIELDS


class TestAuthorInLogs:
    """Le nom de l'utilisateur connecté figure dans la trace de chaque décision et de chaque modification."""

    @pytest.fixture
    def secure_services(self, secure_app):
        return secure_app.extensions["tecpap"]

    def test_validation_and_notification(self, logged_in_client, secure_services, sample_order_data):
        order_id = secure_services.db.create_order(sample_order_data)

        logged_in_client.post(f"/api/orders/{order_id}/validate", json={"validated_by": "Karim"})

        rows = user_log_rows(secure_services, order_id)
        assert ("UPDATE", "Status changed to: validee", "admin") in rows
        assert [row[2] for row in rows if row[0] == "NOTIFY"] == ["admin"]

    def test_rejection(self, logged_in_client, secure_services, sample_order_data):
        order_id = secure_services.db.create_order(sample_order_data)
        logged_in_client.post(f"/api/orders/{order_id}/reject", json={"reason": "Doublon"})
        assert ("UPDATE", "Status changed to: rejetee", "admin") in user_log_rows(secure_services, order_id)

    def test_modification(self, logged_in_client, secure_services, sample_order_data):
        order_id = secure_services.db.create_order(sample_order_data)

        logged_in_client.post(f"/api/orders/{order_id}/update", json={"quantite": 6000})
        logged_in_client.put(f"/api/orders/{order_id}/sage-fields", json={"code_article": "KR-80-90"})

        updates = [row for row in user_log_rows(secure_services, order_id) if row[1].startswith("Updated fields")]
        assert len(updates) >= 2
        assert all(user == "admin" for _, _, user in updates[-2:])

    def test_without_login_no_author_is_invented(self, flask_client, services, sample_order_data):
        order_id = services.db.create_order(sample_order_data)
        flask_client.post(f"/api/orders/{order_id}/update", json={"quantite": 6000})
        updates = [row for row in user_log_rows(services, order_id) if row[1].startswith("Updated fields")]
        assert updates and all(user is None for _, _, user in updates)


class TestProcessEmails:
    """Un seul traitement des emails à la fois."""

    def test_second_request_gets_409(self, flask_client, monkeypatch, caplog):
        def refuse(self, max_emails=10, save_to_db=True):
            raise ProcessingAlreadyRunning()

        monkeypatch.setattr(OrderProcessor, "process_new_emails", refuse)
        with caplog.at_level(logging.INFO, logger="tecpap.web.routes.api"):
            response = flask_client.post("/api/process-emails")

        assert response.status_code == 409
        assert response.get_json() == {"success": False, "error": PROCESSING_RUNNING_MESSAGE}
        assert "déjà en cours" in PROCESSING_RUNNING_MESSAGE
        assert "Échec du traitement des emails" not in caplog.text

    def test_other_errors_stay_500(self, flask_client, monkeypatch):
        def broken(self, max_emails=10, save_to_db=True):
            raise RuntimeError("secret-imap")

        monkeypatch.setattr(OrderProcessor, "process_new_emails", broken)
        response = flask_client.post("/api/process-emails")
        assert response.status_code == 500
        assert "secret-imap" not in response.get_data(as_text=True)


class TestHealthProbe:
    """/healthz vérifie que la base répond."""

    def test_ok(self, flask_client):
        response = flask_client.get("/healthz")
        assert response.status_code == 200
        assert response.get_json()["status"] == "ok"

    def test_unreadable_database_gives_503(self, flask_client, services, test_settings, caplog):
        services.db.close_all()
        with open(test_settings.database_path, "wb") as database:
            database.write(b"ceci n'est pas une base SQLite" * 100)

        with caplog.at_level(logging.WARNING, logger="tecpap.web.routes.api"):
            response = flask_client.get("/healthz")

        assert response.status_code == 503
        assert response.get_json()["status"] == "error"
        assert response.get_json()["reason"] == "database"
        assert "base de données indisponible" in caplog.text

    def test_missing_connection_gives_503(self, flask_client, services, monkeypatch):
        monkeypatch.setattr(type(services.db), "connection", property(lambda self: None))
        assert flask_client.get("/healthz").status_code == 503

    def test_failing_query_gives_503(self, flask_client, services, monkeypatch):
        class Broken:
            def execute(self, *args, **kwargs):
                raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(type(services.db), "connection", property(lambda self: Broken()))
        assert flask_client.get("/healthz").status_code == 503
