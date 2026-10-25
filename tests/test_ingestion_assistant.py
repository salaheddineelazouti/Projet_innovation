"""Assistant WhatsApp, réponses de la base de connaissances et canal WhatsApp : propriétés de sécurité et de cohérence.

Chaque test construit l'assistant sur une base SQLite temporaire ; les modèles de langue et Twilio
sont remplacés par des doublures.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tecpap.channels.whatsapp import WhatsAppReceiver, remove_media_file
from tecpap.chatbot.bot import BotReply, WhatsAppAssistant
from tecpap.chatbot.messages import render
from tecpap.chatbot.tools import PRODUCT_DEPENDENT_FIELDS, AssistantTools, merge_order_fields, order_date
from tecpap.config import Settings
from tecpap.extraction.extractor import RULES_FALLBACK_ANOMALY, RULES_MAX_CONFIDENCE
from tecpap.llm.client import ChatResult
from tecpap.llm.router import ModelRouter
from tecpap.processing.multimodal import KIND_AUDIO, MediaText
from tecpap.rag.answer import (
    REASON_COMMERCIAL,
    KnowledgeAnswerer,
    invented_figures,
    is_commercial_question,
)
from tecpap.rag.documents import Chunk
from tecpap.rag.embeddings import LocalHashEmbedder
from tecpap.rag.retriever import (
    ADDRESS_PLACEHOLDER,
    CLIENT_PLACEHOLDER,
    PERSON_PLACEHOLDER,
    SENDER_PLACEHOLDER,
    URL_PLACEHOLDER,
    OrderExampleRetriever,
    SearchHit,
    anonymize_text,
)
from tests.fakes import FakeLLMClient

ALICE = "whatsapp:+212600000001"
BOB = "whatsapp:+212600000002"


class Clock:
    def __init__(self):
        self.moment = datetime(2026, 10, 25, 9, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, **delta: float) -> None:
        self.moment += timedelta(**delta)


class Chat:
    """Conversation d'un expéditeur ; les espaces insécables des quantités sont remplacées par des espaces."""

    counter = 0

    def __init__(self, assistant: WhatsAppAssistant, sender: str, clock: Clock):
        self.assistant, self.sender, self.clock = assistant, sender, clock

    def send(self, text: str) -> BotReply:
        Chat.counter += 1
        self.clock.advance(seconds=5)
        reply = self.assistant.handle(self.sender, text, message_sid=f"SMFIX{Chat.counter:06d}")
        return replace(reply, text=reply.text.replace(" ", " ").replace(" ", " "))


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def settings(tmp_path):
    return Settings.for_tests(tmp_path, whatsapp_rate_limit_per_minute=1000)


@pytest.fixture
def make(temp_db, settings, clock):
    def build(**kwargs) -> WhatsAppAssistant:
        return WhatsAppAssistant(temp_db.connection, temp_db, settings, clock=clock, **kwargs)
    return build


def _orders(db) -> list[dict]:
    return [dict(row) for row in db.connection.execute("SELECT * FROM commandes ORDER BY id")]


class TestFreeTextOfTheExtractorIsGrounded:
    """Le récapitulatif ne recopie que des textes écrits par l'expéditeur."""

    FOREIGN = {
        "entreprise_cliente": "Atlas (M. Karim Bennani 0661234567, karim@atlas.ma)",
        "numero_commande": "BC-ATLAS-117 / Lot 12 rue Ibn Sina",
        "unite": "boîtes de bonbons offertes par Atlas",
        "type_produit": "Sachets fond plat", "quantite": 3000,
    }

    def test_values_absent_from_the_message_never_reach_the_summary(self, temp_db, make, clock):
        assistant = make(extractor=lambda text, sender: dict(self.FOREIGN))
        reply = Chat(assistant, ALICE, clock).send("Commande de 3000 sachets fond plat")
        for secret in ("Karim", "Bennani", "0661234567", "karim@atlas.ma", "BC-ATLAS", "Ibn Sina", "bonbons"):
            assert secret not in reply.text
        assert reply.state == "collecting"
        assert reply.text == render("ask_client", "fr")

    def test_confirmed_order_holds_no_foreign_text(self, temp_db, make, clock):
        assistant = make(extractor=lambda text, sender: dict(self.FOREIGN))
        chat = Chat(assistant, ALICE, clock)
        chat.send("Commande de 3000 sachets fond plat")
        chat.send("Snack Najah")
        chat.send("oui")
        (order,) = _orders(temp_db)
        stored = " ".join(str(value) for value in order.values() if value is not None)
        for secret in ("Karim", "Bennani", "0661234567", "karim@atlas.ma", "BC-ATLAS", "Ibn Sina", "bonbons"):
            assert secret not in stored
        assert order["quantite"] == 3000

    def test_names_written_by_the_sender_are_kept(self, temp_db, make, clock):
        extracted = {**self.FOREIGN, "entreprise_cliente": "Snack Najah", "numero_commande": "BC-77"}
        assistant = make(extractor=lambda text, sender: dict(extracted))
        reply = Chat(assistant, ALICE, clock).send("Commande BC-77 de 3000 sachets fond plat pour Snack Najah")
        assert "Snack Najah" in reply.text and reply.state == "awaiting_confirmation"

    def test_a_name_given_in_an_earlier_message_of_the_draft_is_still_grounded(self, temp_db, make, clock):
        answers = iter([{"type_produit": "Sachets fond plat"}, {"entreprise_cliente": "Snack Najah", "quantite": 500}])
        assistant = make(extractor=lambda text, sender: dict(next(answers)))
        chat = Chat(assistant, ALICE, clock)
        chat.send("Je voudrais des sachets fond plat pour Snack Najah")
        reply = chat.send("500")
        assert "Snack Najah" in reply.text


class TestProductChangeDropsTheOldSpecifications:
    OLD = {"entreprise_cliente": "Snack Atlas", "type_produit": "Sachets fond plat", "quantite": 2000,
           "unite": "pièces", "code_article": "KB80L32", "nature_produit": "SACHET FOND PLAT KRAFT BRUN 80G L32",
           "type_papier": "Kraft brun", "grammage": 80, "laize": 32, "format_sac": "18x8x32", "type_sac": "SFP",
           "impression_client": "logo Atlas"}

    @pytest.fixture
    def history(self, temp_db):
        order_id = temp_db.create_order({**self.OLD, "source": "whatsapp", "whatsapp_from": ALICE,
                                         "prix_unitaire": 0.4})
        temp_db.update_order_status(order_id, "validee")
        return order_id

    @staticmethod
    def _assert_new_product_only(db, order_id: int) -> None:
        order = db.get_order(order_id)
        assert order["produit_type"] == "Sac fond carré avec poignées plates"
        for name in PRODUCT_DEPENDENT_FIELDS:
            assert order.get(name) in (None, ""), (name, order.get(name))

    def test_changing_the_product_after_a_reorder_drops_the_old_specifications(self, temp_db, make, clock, history):
        chat = Chat(make(), ALICE, clock)
        assert "Sachets fond plat" in chat.send("comme d'habitude").text
        reply = chat.send("plutôt des Sac fond carré avec poignées plates")
        assert reply.state == "awaiting_confirmation"
        chat.send("oui")
        order = _orders(temp_db)[-1]
        assert order["id"] != history and order["quantite"] == 2000
        self._assert_new_product_only(temp_db, order["id"])

    def test_changing_the_product_in_the_reorder_message_itself(self, temp_db, make, clock, history):
        chat = Chat(make(), ALICE, clock)
        chat.send("comme d'habitude mais en sac fond carré avec poignées plates")
        chat.send("oui")
        self._assert_new_product_only(temp_db, _orders(temp_db)[-1]["id"])

    def test_the_same_product_keeps_the_specifications(self, temp_db, make, clock, history):
        chat = Chat(make(), ALICE, clock)
        chat.send("comme d'habitude")
        chat.send("oui")
        order = _orders(temp_db)[-1]
        assert (order["code_article"], order["grammage"], order["laize"]) == ("KB80L32", 80, 32)

    def test_merge_order_fields(self):
        order = {"type_produit": "Sachets fond plat", **{name: "ancien" for name in PRODUCT_DEPENDENT_FIELDS},
                 "quantite": 10}
        merged = merge_order_fields(order, {"type_produit": "Sac fond carré sans poignées", "grammage": 90})
        assert merged is order
        assert merged["grammage"] == 90 and merged["quantite"] == 10
        assert not [name for name in PRODUCT_DEPENDENT_FIELDS if name != "grammage" and name in merged]
        same = merge_order_fields({"type_produit": "Sachets fond plat", "code_article": "X"},
                                  {"type_produit": "Sachets fond plat", "quantite": 5})
        assert same["code_article"] == "X"
        unset = merge_order_fields({"code_article": "X"}, {"quantite": 5})
        assert unset == {"code_article": "X", "quantite": 5}


class TestRulesOnlyOrdersKeepALowConfidence:
    def test_confirmed_order_read_by_rules_stays_under_the_cap(self, temp_db, make, clock):
        def extract(text, sender):
            return {"entreprise_cliente": "Snack Najah", "type_produit": "Sachets fond plat", "quantite": 3000,
                    "est_bon_commande": True, "modele_llm": None, "anomalies": [RULES_FALLBACK_ANOMALY]}

        chat = Chat(make(extractor=extract), ALICE, clock)
        chat.send("Snack Najah : 3000 sachets fond plat")
        chat.send("oui")
        (order,) = _orders(temp_db)
        assert order["confiance"] <= RULES_MAX_CONFIDENCE
        assert RULES_FALLBACK_ANOMALY in temp_db.get_order(order["id"])["anomalies_list"]

    def test_an_order_read_by_a_model_is_not_capped(self, temp_db, make, clock):
        def extract(text, sender):
            return {"entreprise_cliente": "Snack Najah", "type_produit": "Sachets fond plat", "quantite": 3000,
                    "est_bon_commande": True, "modele_llm": "gpt-4o", "confiance": 95, "anomalies": []}

        chat = Chat(make(extractor=extract), ALICE, clock)
        chat.send("Snack Najah : 3000 sachets fond plat")
        chat.send("oui")
        (order,) = _orders(temp_db)
        assert order["confiance"] > RULES_MAX_CONFIDENCE


class TestOnlyOrdersSentFromTheNumberAreVisible:
    def test_orders_of_the_client_sent_by_email_stay_hidden(self, temp_db):
        email_order = temp_db.create_order({"entreprise_cliente": "Snack Atlas", "quantite": 10,
                                            "email_from": "achats@atlas.ma", "numero_commande": "EM-1",
                                            "message_sid": "<1@x>"})
        own = temp_db.create_order({"entreprise_cliente": "Snack Atlas", "quantite": 20, "source": "whatsapp",
                                    "whatsapp_from": ALICE, "message_sid": "SM1"})
        temp_db.update_order_status(own, "validee")
        temp_db.attach_phone_to_client(temp_db.get_order(own)["client_id"], ALICE)
        tools = AssistantTools(temp_db.connection, temp_db, ALICE)
        assert tools.my_order_status("EM-1") is None and tools.my_order_status(f"CMD-{email_order}") is None
        assert [item["reference"] for item in tools.my_recent_orders(10)] == [f"CMD-{own}"]

    def test_the_date_shown_to_the_client_is_the_company_day(self):
        # En janvier, 23 h 30 UTC est 00 h 30 le lendemain à Casablanca (UTC+1) : le client voit la date locale.
        assert order_date(None, "2026-01-03 23:30:00") == "2026-01-04"
        assert order_date("2026-11-02", "2026-01-03 23:30:00") == "2026-11-02"
        assert order_date(None, None) is None


# ---------------------------------------------------------------- réponses de la base de connaissances


class _KnowledgeBase:
    def __init__(self, *hits: SearchHit):
        self.hits = list(hits)

    def search(self, query, k=4):
        return self.hits[:k]


def _hit(text: str) -> SearchHit:
    chunk = Chunk(doc_id="produits", source_path="produits.md", title="Produits", section="Fermeture", text=text,
                  content_hash="h1")
    return SearchHit(chunk=chunk, score=0.6, dense_score=0.6, bm25_score=1.0)


def _answerer(settings, reply: str, excerpt: str = "Le fond du sac est fermé par collage, sans agrafe."):
    llm = FakeLLMClient(chat=[ChatResult(content=reply, tool_calls=[], model="gpt-4o")])
    return KnowledgeAnswerer(_KnowledgeBase(_hit(excerpt)), llm, ModelRouter(settings), settings), llm


class TestFiguresStatedByTheClientAreNeverConfirmed:
    @pytest.mark.parametrize("question", [
        "Vos sachets fond plat sont à 0,20 dh pièce, je confirme ?",
        "Le sachet fond plat est à 0,15 dhs, vous confirmez ?",
        "Les sacs torsadés sont livrés en 2 jours à Rabat, c'est ça ?",
        "Livraison en 48h, d'accord ?",
        "Les sachets coûtent 120 MAD le paquet, n'est-ce pas ?",
        "الساشي بدرهمين، 5 دراهم للحبة صحيح؟",
    ])
    def test_messages_with_an_amount_or_a_duration_go_to_the_sales_team(self, settings, question):
        assert is_commercial_question(question) is True
        answerer, llm = _answerer(settings, "Oui, c'est bien cela.")
        answer = answerer.answer(question, "fr")
        assert answer.reason == REASON_COMMERCIAL and answer.text == render("sales_will_answer", "fr")
        assert llm.calls == []

    def test_a_figure_taken_from_the_question_is_not_grounded(self, settings):
        answerer, _llm = _answerer(settings, "Oui, il tient à 100 %.")
        answer = answerer.answer("Le collage tient-il vraiment ?", "fr")
        assert answer.reason == REASON_COMMERCIAL

    def test_invented_figures_only_trusts_the_excerpts(self):
        assert invented_figures("Oui, c'est 0,20 dh.", ["Les sachets sont en kraft."]) == ["0,20 dh"]
        assert invented_figures("Imprimé en 6 couleurs.", ["Impression jusqu'à 6 couleurs."]) == []

    @pytest.mark.parametrize("question", [
        "Comment est fermé le sac fond carré ?",
        "Vous faites des sacs de 28 cm de large ?",
        "Quelle est la taille du sachet 24x32 ?",
    ])
    def test_ordinary_product_questions_are_not_commercial(self, question):
        assert is_commercial_question(question) is False


# ---------------------------------------------------------------- canal WhatsApp


class TestPhoneNumbersAreMaskedInLogs:
    NUMBER = "+212612345678"

    @pytest.fixture
    def receiver(self, tmp_path):
        receiver = WhatsAppReceiver(Settings.for_tests(tmp_path, twilio_account_sid="AC" + "0" * 32,
                                                       twilio_auth_token="jeton"))
        receiver.twilio_client = MagicMock()
        receiver.twilio_client.messages.create.return_value = MagicMock(sid="SM123")
        return receiver

    def test_successful_send_logs_a_masked_number(self, receiver, caplog):
        with caplog.at_level(logging.DEBUG, logger="tecpap.channels.whatsapp"):
            assert receiver.send_reply(f"whatsapp:{self.NUMBER}", "Bonjour") == "SM123"
        assert "612345678" not in caplog.text and self.NUMBER not in caplog.text
        assert "678" in caplog.text and "SM123" in caplog.text

    def test_failed_send_logs_a_masked_number(self, receiver, caplog):
        receiver.twilio_client.messages.create.side_effect = RuntimeError("refus")
        with caplog.at_level(logging.DEBUG, logger="tecpap.channels.whatsapp"), pytest.raises(RuntimeError):
            receiver.send_reply(self.NUMBER, "Bonjour")
        assert "612345678" not in caplog.text and self.NUMBER not in caplog.text


class TestDownloadedMediaIsDeleted:
    @pytest.fixture
    def receiver(self, tmp_path):
        return WhatsAppReceiver(Settings.for_tests(tmp_path, twilio_account_sid="AC" + "0" * 32,
                                                   twilio_auth_token="jeton"), llm=FakeLLMClient())

    def _download_to(self, receiver, tmp_path) -> Path:
        path = tmp_path / "doc_20261025.pdf"
        path.write_bytes(b"%PDF-1.4")
        receiver.download_media = lambda url, filename=None: str(path)
        return path

    def test_file_is_removed_after_reading(self, receiver, tmp_path):
        path = self._download_to(receiver, tmp_path)
        receiver.media.process_file = lambda filepath, declared_mime=None: MediaText("pdf", "Bon de commande", None)
        result = receiver.process_media("https://api.twilio.com/x/Media/ME1", "application/pdf")
        assert result.text == "Bon de commande" and not path.exists()

    def test_file_is_removed_when_reading_fails(self, receiver, tmp_path):
        path = self._download_to(receiver, tmp_path)

        def broken(filepath, declared_mime=None):
            raise RuntimeError("lecture impossible")

        receiver.media.process_file = broken
        with pytest.raises(RuntimeError):
            receiver.process_media("https://api.twilio.com/x/Media/ME1", "application/pdf")
        assert not path.exists()

    def test_audio_file_is_removed(self, tmp_path):
        llm = FakeLLMClient(transcribe=["Bghit 3000 sachet"])
        receiver = WhatsAppReceiver(Settings.for_tests(tmp_path, twilio_account_sid="AC" + "0" * 32,
                                                       twilio_auth_token="jeton"), llm=llm)
        path = tmp_path / "audio_20261025.ogg"
        path.write_bytes(b"OggS")
        receiver.download_media = lambda url, filename=None: str(path)
        receiver.media.process_file = lambda filepath, declared_mime=None: MediaText(KIND_AUDIO, "Bghit 3000 sachet", None)
        assert receiver.process_media("https://api.twilio.com/x/Media/ME2", "audio/ogg").text == "Bghit 3000 sachet"
        assert not path.exists()

    def test_remove_media_file(self, tmp_path):
        path = tmp_path / "x.pdf"
        path.write_bytes(b"x")
        assert remove_media_file(str(path)) is True and not path.exists()
        assert remove_media_file(str(path)) is False


# ---------------------------------------------------------------- exemples de commandes anonymisés


class TestExamplesDoNotCarryDataOfOtherClients:
    MESSAGE = ("Bonjour, ici Atlas (M. Karim Bennani), responsable achats. Livrer au 12 rue Ibn Sina Casablanca, "
               "3000 sachets fond plat. ICE 001234567000089. www.atlas.ma")

    def test_short_name_contact_and_address_are_masked(self):
        text = anonymize_text(self.MESSAGE, ["Boulangerie Atlas SARL"])
        for secret in ("Atlas", "Karim", "Bennani", "Ibn Sina", "001234567000089", "atlas.ma"):
            assert secret not in text, secret
        assert "3000 sachets fond plat" in text
        for marker in (CLIENT_PLACEHOLDER, PERSON_PLACEHOLDER, ADDRESS_PLACEHOLDER, URL_PLACEHOLDER):
            assert marker in text

    def test_signature_after_the_closing_formula_is_removed(self):
        text = anonymize_text("Commande de 3000 sachets.\nCordialement,\nKarim Bennani\nDirecteur achats\nAtlas SARL")
        assert text.endswith("Cordialement") and "Karim" not in text and "Directeur" not in text

    def test_email_header_line_is_masked(self):
        text = anonymize_text("DE: Karim Bennani <k@atlas.ma>\nSUJET: Commande\nCONTENU:\n3000 sachets")
        assert f"DE : {SENDER_PLACEHOLDER}" in text and "Karim" not in text and "atlas.ma" not in text
        assert "SUJET: Commande" in text and "3000 sachets" in text

    @pytest.mark.parametrize("message", [
        "ana Karim Bennani bghit 3000 sachet", "Je suis Karim Bennani, 3000 sachets", "Contact : Mme Alaoui, 3000 sachets",
        "الحاج محمد العلوي بغيت 3000 ساشي",
    ])
    def test_people_introduced_by_a_title_or_a_presentation_are_masked(self, message):
        text = anonymize_text(message)
        assert PERSON_PLACEHOLDER in text
        for name in ("Karim", "Bennani", "Alaoui", "العلوي"):
            assert name not in text

    @pytest.mark.parametrize("message", ["حي المحمدي، شارع الحسن الثاني", "quartier Maârif, 12 avenue Hassan II",
                                         "lotissement Al Massira"])
    def test_postal_addresses_are_masked(self, message):
        assert ADDRESS_PLACEHOLDER in anonymize_text(message)

    def test_quantities_prices_and_codes_stay_readable(self):
        text = "Commande 5000000 sacs, 10 000 pièces, code KB80L32, prix 0,45 MAD, lot de 500, BC-2026-0417"
        assert anonymize_text(text) == text

    def test_a_catalog_word_in_a_client_name_is_not_masked_everywhere(self):
        text = anonymize_text("3000 sachets fond plat pour Sachets Express, ici Express", ["Sachets Express"])
        assert text == f"3000 sachets fond plat pour {CLIENT_PLACEHOLDER}, ici {CLIENT_PLACEHOLDER}"

    def test_retrieved_examples_are_anonymized_with_the_extra_rules(self, temp_db):
        order_id = temp_db.create_order({
            "entreprise_cliente": "Boulangerie Atlas SARL", "type_produit": "Sachets fond plat", "quantite": 3000,
            "email_from": "k.bennani@atlas.ma", "message_brut": self.MESSAGE, "message_sid": "<1@x>"})
        temp_db.update_order_status(order_id, "validee")
        retriever = OrderExampleRetriever(temp_db.connection, LocalHashEmbedder())
        (example,) = retriever.similar_validated_orders("3000 sachets fond plat")
        for secret in ("Atlas", "Karim", "Bennani", "Ibn Sina", "001234567000089"):
            assert secret not in example.message and secret not in repr(example.order)
        assert CLIENT_PLACEHOLDER in example.message and "3000 sachets fond plat" in example.message
