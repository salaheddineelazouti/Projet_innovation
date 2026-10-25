"""Tests de la vérification de signature Twilio sur le webhook WhatsApp et de l'événement de sécurité d'un refus."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from twilio.request_validator import RequestValidator
from werkzeug.middleware.proxy_fix import ProxyFix

from tecpap.config import Settings
from tecpap.security.events import KIND_INVALID_SIGNATURE, recent_security_events
from tecpap.security.redaction import mask_phone
from tecpap.web import create_app
from tecpap.web.routes.webhooks import PROCESSING_ERROR_REPLY

AUTH_TOKEN = "jeton-twilio-de-test"
WEBHOOK = "/webhook/whatsapp"
LOCAL_URL = "http://localhost/webhook/whatsapp"
PUBLIC_BASE_URL = "https://tecpap.example.org"
MESSAGE = {
    "From": "whatsapp:+212612345678",
    "To": "whatsapp:+14155238886",
    "Body": "bghit 2000 sachet",
    "NumMedia": "0",
    "MessageSid": "SM0123456789abcdef0123456789abcdef",
}


def sign(url: str, params: dict[str, str], token: str = AUTH_TOKEN) -> str:
    """Signature calculée comme Twilio la calcule (HMAC-SHA1 de l'URL et des paramètres)."""
    return RequestValidator(token).compute_signature(url, params)


@pytest.fixture
def make_app(tmp_path):
    """Construit une application dont la signature Twilio est vérifiée ; fermée à la fin du test."""
    apps = []

    def build(**overrides):
        values = {"twilio_validate_signature": True, "twilio_auth_token": AUTH_TOKEN}
        values.update(overrides)
        app = create_app(Settings.for_tests(tmp_path / f"app{len(apps)}", **values))
        apps.append(app)
        return app

    yield build
    for app in apps:
        app.extensions["tecpap"].close()


@pytest.fixture
def extractor():
    """Traitement des messages simulé : le test vérifie seulement s'il a lieu (aucun appel au modèle de langue)."""
    with patch("tecpap.web.routes.webhooks.handle_incoming_message", return_value="Message reçu.") as handler:
        yield handler


class TestSignature:
    """Signature X-Twilio-Signature."""

    def test_valid_signature(self, make_app, extractor):
        client = make_app().test_client()
        response = client.post(WEBHOOK, data=MESSAGE, headers={"X-Twilio-Signature": sign(LOCAL_URL, MESSAGE)})
        assert response.status_code == 200
        assert response.content_type.startswith("application/xml")
        extractor.assert_called_once()

    def test_missing_signature(self, make_app, extractor, caplog):
        client = make_app().test_client()
        with caplog.at_level(logging.WARNING, logger="tecpap.web.routes.webhooks"):
            response = client.post(WEBHOOK, data=MESSAGE)
        assert response.status_code == 403
        extractor.assert_not_called()
        assert "signature Twilio absente" in caplog.text

    def test_invalid_signature(self, make_app, extractor, caplog):
        client = make_app().test_client()
        with caplog.at_level(logging.WARNING, logger="tecpap.web.routes.webhooks"):
            response = client.post(WEBHOOK, data=MESSAGE, headers={"X-Twilio-Signature": "c2lnbmF0dXJlLWZhdXNzZQ=="})
        assert response.status_code == 403
        extractor.assert_not_called()
        assert "signature Twilio invalide" in caplog.text

    def test_signature_with_another_token(self, make_app, extractor):
        client = make_app().test_client()
        signature = sign(LOCAL_URL, MESSAGE, token="autre-jeton")
        assert client.post(WEBHOOK, data=MESSAGE, headers={"X-Twilio-Signature": signature}).status_code == 403

    def test_tampered_message(self, make_app, extractor):
        client = make_app().test_client()
        signature = sign(LOCAL_URL, MESSAGE)
        tampered = dict(MESSAGE, Body="bghit 200000 sachet")
        assert client.post(WEBHOOK, data=tampered, headers={"X-Twilio-Signature": signature}).status_code == 403
        extractor.assert_not_called()

    @pytest.mark.parametrize("chatbot_enabled", [True, False])
    def test_nothing_is_recorded_without_signature(self, make_app, chatbot_enabled):
        app = make_app(chatbot_enabled=chatbot_enabled)
        assert app.test_client().post(WEBHOOK, data=MESSAGE).status_code == 403
        services = app.extensions["tecpap"]
        assert services.db.get_all_orders() == []
        assert services.db.connection.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 0

    @pytest.mark.parametrize(("headers", "state"), [({}, "absente"),
                                                    ({"X-Twilio-Signature": "c2lnbmF0dXJlLWZhdXNzZQ=="}, "invalide")])
    def test_refusal_is_a_security_event(self, make_app, extractor, headers, state):
        app = make_app()
        assert app.test_client().post(WEBHOOK, data=MESSAGE, headers=headers).status_code == 403
        (event,) = recent_security_events(app.extensions["tecpap"].db.connection)
        assert (event["kind"], event["channel"]) == (KIND_INVALID_SIGNATURE, "whatsapp")
        assert event["sender"] == mask_phone("+212612345678")
        assert event["details"].startswith(f"Signature Twilio {state} (adresse ")

    def test_signed_message_and_missing_token_leave_no_signature_event(self, make_app, extractor):
        signed = make_app()
        signed.test_client().post(WEBHOOK, data=MESSAGE, headers={"X-Twilio-Signature": sign(LOCAL_URL, MESSAGE)})
        unconfigured = make_app(twilio_auth_token=None)
        assert unconfigured.test_client().post(WEBHOOK, data=MESSAGE).status_code == 503
        for app in (signed, unconfigured):
            connection = app.extensions["tecpap"].db.connection
            assert recent_security_events(connection, kind=KIND_INVALID_SIGNATURE) == []

    def test_event_failure_keeps_the_refusal(self, make_app, extractor, monkeypatch, caplog):
        def broken(*args, **kwargs):
            raise RuntimeError("base indisponible")

        monkeypatch.setattr("tecpap.web.routes.webhooks.log_security_event", broken)
        with caplog.at_level(logging.WARNING, logger="tecpap.web.routes.webhooks"):
            assert make_app().test_client().post(WEBHOOK, data=MESSAGE).status_code == 403
        assert "Événement de sécurité non enregistré" in caplog.text

    def test_missing_auth_token(self, make_app, extractor, caplog):
        client = make_app(twilio_auth_token=None).test_client()
        with caplog.at_level(logging.ERROR, logger="tecpap.web.routes.webhooks"):
            response = client.post(WEBHOOK, data=MESSAGE, headers={"X-Twilio-Signature": sign(LOCAL_URL, MESSAGE)})
        assert response.status_code == 503
        extractor.assert_not_called()
        assert "TWILIO_AUTH_TOKEN" in caplog.text

    def test_missing_auth_token_logged_at_startup(self, make_app, caplog):
        with caplog.at_level(logging.ERROR, logger="tecpap.web.routes.webhooks"):
            make_app(twilio_auth_token=None)
        assert "refusera tous les messages" in caplog.text

    def test_validation_disabled(self, make_app, extractor):
        client = make_app(twilio_validate_signature=False).test_client()
        assert client.post(WEBHOOK, data=MESSAGE).status_code == 200


class TestPublicUrl:
    """URL signée derrière un tunnel ou un proxy inverse."""

    def test_public_base_url_is_used(self, make_app, extractor):
        client = make_app(public_base_url=PUBLIC_BASE_URL).test_client()
        signature = sign(PUBLIC_BASE_URL + WEBHOOK, MESSAGE)
        assert client.post(WEBHOOK, data=MESSAGE, headers={"X-Twilio-Signature": signature}).status_code == 200

    def test_local_url_refused_when_public_url_set(self, make_app, extractor):
        client = make_app(public_base_url=PUBLIC_BASE_URL).test_client()
        signature = sign(LOCAL_URL, MESSAGE)
        assert client.post(WEBHOOK, data=MESSAGE, headers={"X-Twilio-Signature": signature}).status_code == 403

    def test_trailing_slash_in_public_url(self, make_app, extractor):
        client = make_app(public_base_url=PUBLIC_BASE_URL + "/").test_client()
        signature = sign(PUBLIC_BASE_URL + WEBHOOK, MESSAGE)
        assert client.post(WEBHOOK, data=MESSAGE, headers={"X-Twilio-Signature": signature}).status_code == 200

    def test_query_string_is_signed(self, make_app, extractor):
        client = make_app(public_base_url=PUBLIC_BASE_URL).test_client()
        url = WEBHOOK + "?canal=whatsapp"
        signature = sign(PUBLIC_BASE_URL + url, MESSAGE)
        assert client.post(url, data=MESSAGE, headers={"X-Twilio-Signature": signature}).status_code == 200
        without_query = sign(PUBLIC_BASE_URL + WEBHOOK, MESSAGE)
        assert client.post(url, data=MESSAGE, headers={"X-Twilio-Signature": without_query}).status_code == 403

    def test_forwarded_headers_trusted_behind_proxy(self, make_app, extractor):
        app = make_app(trust_proxy_headers=True)
        assert isinstance(app.wsgi_app, ProxyFix)
        headers = {
            "X-Forwarded-Proto": "https", "X-Forwarded-Host": "tecpap.example.org", "X-Forwarded-For": "203.0.113.7",
            "X-Twilio-Signature": sign(PUBLIC_BASE_URL + WEBHOOK, MESSAGE),
        }
        assert app.test_client().post(WEBHOOK, data=MESSAGE, headers=headers).status_code == 200

    def test_forwarded_headers_ignored_by_default(self, make_app, extractor):
        app = make_app()
        assert not isinstance(app.wsgi_app, ProxyFix)
        headers = {
            "X-Forwarded-Proto": "https", "X-Forwarded-Host": "tecpap.example.org",
            "X-Twilio-Signature": sign(PUBLIC_BASE_URL + WEBHOOK, MESSAGE),
        }
        assert app.test_client().post(WEBHOOK, data=MESSAGE, headers=headers).status_code == 403


class TestErrors:
    """Une erreur de traitement ne renvoie jamais son détail à l'expéditeur."""

    def test_generic_twiml_reply(self, flask_client, services, monkeypatch, caplog):
        secret = "Traceback interne : clé sk-secrete dans /srv/tecpap/.env"

        def fail(*args, **kwargs):
            raise RuntimeError(secret)

        monkeypatch.setattr(services.whatsapp, "process_incoming_message", fail)
        with caplog.at_level(logging.ERROR, logger="tecpap.web.routes.webhooks"):
            response = flask_client.post(WEBHOOK, data=MESSAGE)
        body = response.get_data(as_text=True)
        assert response.status_code == 200
        assert response.content_type.startswith("application/xml")
        assert "<Message>" in body
        assert PROCESSING_ERROR_REPLY in body
        assert secret not in body
        assert "RuntimeError" not in body
        assert "Erreur du webhook WhatsApp" in caplog.text
        assert secret in caplog.text

    def test_webhook_exempt_from_login_and_csrf(self, make_app, extractor):
        app = make_app(auth_enabled=True, csrf_enabled=True, admin_password_hash="pbkdf2:sha256:1000$sel$valeur")
        response = app.test_client().post(WEBHOOK, data=MESSAGE,
                                          headers={"X-Twilio-Signature": sign(LOCAL_URL, MESSAGE)})
        assert response.status_code == 200
