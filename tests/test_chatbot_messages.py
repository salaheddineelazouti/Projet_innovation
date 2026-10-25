"""Tests des modèles de réponse de l'assistant WhatsApp (tecpap.chatbot.messages)."""

from __future__ import annotations

import re
import string

import pytest

from tecpap.catalog import PRODUCTS
from tecpap.chatbot import messages
from tecpap.chatbot.messages import (
    LANGUAGES,
    STATUS_LABELS,
    TEMPLATE_KEYS,
    format_date,
    format_quantity,
    normalize_reply_language,
    order_lines,
    product_label,
    product_menu,
    reason_line,
    render,
    status_label,
    status_lines,
    unit_label,
)
from tecpap.extraction.rules import normalize_product_type

REQUIRED_KEYS = {
    "greeting", "help", "ask_client", "ask_product", "ask_quantity", "quantity_invalid", "summary",
    "reorder_intro", "confirm_hint", "ask_change", "submitted", "cancelled", "cancel_forwarded", "status_list",
    "status_none", "status_not_found", "handoff", "handoff_ack", "blocked", "no_answer", "sales_will_answer",
    "rate_limited", "media_failed", "error", "reorder_none", "draft_expired", "excerpt_answer", "catalog_list",
    "order_validated", "order_rejected",
}

# Valeurs d'exemple pour remplir chaque modèle.
SAMPLE_VALUES = {"reference": "CMD-12", "lines": "- ligne", "excerpt": "texte", "products": "1. produit",
                 "reason": ""}

# Caractères pictographiques (émojis) : seul ✅ est accepté, dans la confirmation d'enregistrement.
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿]")


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


class TestTemplateInventory:
    def test_every_required_key_exists(self):
        assert REQUIRED_KEYS <= TEMPLATE_KEYS

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_every_language_defines_every_key(self, language):
        assert set(messages._TEMPLATES[language]) == set(TEMPLATE_KEYS)

    @pytest.mark.parametrize("key", sorted(REQUIRED_KEYS))
    def test_placeholders_are_identical_across_languages(self, key):
        reference = _fields(messages._TEMPLATES["fr"][key])
        for language in LANGUAGES:
            assert _fields(messages._TEMPLATES[language][key]) == reference, (key, language)

    @pytest.mark.parametrize("language", LANGUAGES)
    @pytest.mark.parametrize("key", sorted(REQUIRED_KEYS))
    def test_every_template_renders(self, key, language):
        text = render(key, language, **SAMPLE_VALUES)
        assert text.strip()
        assert "{" not in text and "}" not in text

    @pytest.mark.parametrize("language", LANGUAGES)
    @pytest.mark.parametrize("key", sorted(REQUIRED_KEYS - {"greeting", "help", "ask_product", "catalog_list"}))
    def test_templates_stay_short(self, key, language):
        assert len(render(key, language, **SAMPLE_VALUES)) <= 260

    @pytest.mark.parametrize("language", LANGUAGES)
    @pytest.mark.parametrize("key", sorted(REQUIRED_KEYS))
    def test_no_emoji_except_the_check_mark(self, key, language):
        text = render(key, language, **SAMPLE_VALUES).replace("✅", "")
        assert not EMOJI_RE.search(text)

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_check_mark_only_in_submission(self, language):
        assert render("submitted", language, reference="CMD-1").startswith("✅")
        for key in REQUIRED_KEYS - {"submitted"}:
            assert "✅" not in render(key, language, **SAMPLE_VALUES)

    def test_arabic_templates_are_written_in_arabic_script(self):
        for key in REQUIRED_KEYS:
            assert re.search("[؀-ۿ]", render(key, "ar", **SAMPLE_VALUES)), key

    def test_darija_templates_are_written_in_latin_script(self):
        for key in REQUIRED_KEYS:
            text = render(key, "darija", **SAMPLE_VALUES)
            assert not re.search("[؀-ۿ]", text), key


class TestRender:
    def test_unknown_key_raises(self):
        with pytest.raises(KeyError):
            render("inexistant", "fr")

    def test_missing_value_raises(self):
        with pytest.raises(KeyError):
            render("submitted", "fr")

    @pytest.mark.parametrize("language", [None, "", "en", "unknown", "es", 42, "FR "])
    def test_unknown_language_falls_back_to_french(self, language):
        assert render("handoff", language) == render("handoff", "fr")

    @pytest.mark.parametrize("code, expected", [
        ("darija_latin", "darija"), ("darija_ar", "darija"), ("darija", "darija"), ("ar", "ar"), ("AR", "ar"),
        (" fr ", "fr"), ("en", "fr"), ("unknown", "fr"), (None, "fr"),
    ])
    def test_normalize_reply_language(self, code, expected):
        assert normalize_reply_language(code) == expected

    def test_values_with_braces_are_inserted_verbatim(self):
        text = render("submitted", "fr", reference="{lines} {0} {__class__}")
        assert "{lines} {0} {__class__}" in text

    def test_greeting_contains_help(self):
        for language in LANGUAGES:
            assert render("help", language) in render("greeting", language)

    def test_ask_product_lists_the_four_products(self):
        for language in LANGUAGES:
            text = render("ask_product", language)
            for number in range(1, len(PRODUCTS) + 1):
                assert f"\n{number}. " in text

    def test_intro_defaults_to_empty(self):
        assert render("summary", "fr", lines="- x").startswith("Récapitulatif")

    def test_confirmation_words_match_the_intent_vocabulary(self):
        from tecpap.chatbot.intents import detect_intent

        words = {"fr": ("oui", "non", "annuler"), "darija": ("wakha", "la", "annuler"), "ar": ("نعم", "لا", "إلغاء")}
        expected = ("confirm_yes", "confirm_no", "cancel")
        for language, (yes, no, cancel) in words.items():
            summary = render("summary", language, lines="- x")
            assert yes.lower() in summary.lower() and cancel.lower() in summary.lower()
            for word, intent in zip((yes, no, cancel), expected, strict=True):
                assert detect_intent(word, suspicious=False).intent == intent

    def test_human_keyword_of_each_language_is_recognised(self):
        from tecpap.chatbot.intents import detect_intent

        for word in ("conseiller", "مستشار"):
            assert detect_intent(word, suspicious=False).intent == "human"


class TestFormatting:
    @pytest.mark.parametrize("value, expected", [
        (10000, "10 000"), (10000.0, "10 000"), (1500000, "1 500 000"), (2.5, "2,5"), (1234.5, "1 234,5"),
        (0, "0"), (-300, "-300"), (999, "999"),
    ])
    def test_format_quantity(self, value, expected):
        assert format_quantity(value) == expected

    @pytest.mark.parametrize("value", [None, True])
    def test_format_quantity_of_non_numbers(self, value):
        assert format_quantity(value) in ("—", "True")

    def test_format_quantity_of_text_is_kept(self):
        assert format_quantity("beaucoup") == "beaucoup"

    @pytest.mark.parametrize("value, expected", [
        ("2026-11-15", "15/11/2026"), ("2026-11-15 10:20:30", "15/11/2026"), ("", "—"), (None, "—"),
        ("demain", "demain"), ("aaaa-bb-cc", "aaaa-bb-cc"),
    ])
    def test_format_date(self, value, expected):
        assert format_date(value) == expected

    def test_product_menu_is_numbered_in_catalog_order(self):
        lines = product_menu("fr").splitlines()
        assert len(lines) == len(PRODUCTS)
        for index, (line, product) in enumerate(zip(lines, PRODUCTS, strict=True), start=1):
            assert line.startswith(f"{index}. {product['type']}")

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_product_labels_map_back_to_the_catalog(self, language):
        for product in PRODUCTS:
            label = product_label(product["type"], language)
            assert normalize_product_type(label) == product["type"], (language, label)

    def test_unknown_product_label_is_kept(self):
        assert product_label("Gobelet carton", "ar") == "Gobelet carton"
        assert product_label(None, "fr") == "—"

    @pytest.mark.parametrize("unit, fr, darija, ar", [
        ("US", "pièces", "7ba", "قطعة"), ("pièces", "pièces", "7ba", "قطعة"), (None, "pièces", "7ba", "قطعة"),
        ("kg", "kg", "kilo", "كلغ"), ("rouleaux", "rouleaux", "rouleaux", "rouleaux"),
    ])
    def test_unit_label(self, unit, fr, darija, ar):
        assert (unit_label(unit, "fr"), unit_label(unit, "darija"), unit_label(unit, "ar")) == (fr, darija, ar)

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_status_labels_cover_visible_statuses(self, language):
        assert set(STATUS_LABELS[language]) == {"en_attente", "validee", "rejetee"}

    def test_status_label_of_unknown_status_is_kept(self):
        assert status_label("archivee", "fr") == "archivee"
        assert status_label(None, "fr") == "—"

    def test_order_lines(self):
        order = {"entreprise_cliente": "Snack Atlas", "type_produit": "Sachets fond plat", "quantite": 3000,
                 "unite": "US", "date_livraison": "2026-12-01", "numero_commande": "BC-7"}
        text = order_lines(order, "fr")
        assert text.splitlines() == [
            "- Client : Snack Atlas", "- Produit : Sachets fond plat", "- Quantité : 3 000 pièces",
            "- Livraison souhaitée : 01/12/2026", "- Référence : BC-7",
        ]

    def test_order_lines_in_arabic(self):
        text = order_lines({"entreprise_cliente": "مكتبة القلم", "type_produit": "Sac fond carré avec poignées plates",
                            "quantite": 2000}, "ar")
        assert "الزبون: مكتبة القلم" in text
        assert "كيس بقاع مربع بمقابض مسطحة" in text
        assert "2 000 قطعة" in text

    def test_order_lines_with_missing_fields(self):
        assert "—" in order_lines({}, "darija")

    def test_status_lines(self):
        orders = [{"reference": "CMD-3", "date": "2026-10-20", "product": "Sac fond carré sans poignées",
                   "quantity": 2500, "unit": "US", "status": "validee"},
                  {"reference": "BC-1", "date": None, "product": None, "quantity": None, "unit": None,
                   "status": "en_attente"}]
        lines = status_lines(orders, "fr").splitlines()
        assert lines[0] == "- CMD-3 (20/10/2026) : 2 500 pièces, Sac fond carré sans poignées — validée"
        assert lines[1].startswith("- BC-1 (—)") and lines[1].endswith("en attente de validation")
        assert "tvalidat" in status_lines(orders, "darija")
        assert "مؤكدة" in status_lines(orders, "ar")

    def test_status_lines_of_empty_list(self):
        assert status_lines([], "fr") == ""


def test_package_exports_are_loaded_on_demand():
    import tecpap.chatbot as package
    from tecpap.chatbot import bot, conversation, tools

    assert package.WhatsAppAssistant is bot.WhatsAppAssistant
    assert package.ConversationStore is conversation.ConversationStore
    assert package.AssistantTools is tools.AssistantTools
    assert set(package.__all__) >= {"WhatsAppAssistant", "BotReply", "ConversationStore", "render"}
    with pytest.raises(AttributeError):
        package.inexistant  # noqa: B018


class TestOrderNotifications:
    """Notifications de validation et de refus envoyées par l'équipe commerciale."""

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_validation_contains_the_reference_and_the_lines(self, language):
        lines = order_lines({"entreprise_cliente": "Snack Atlas", "type_produit": PRODUCTS[0]["type"],
                             "quantite": 3000}, language)
        text = render("order_validated", language, reference="CMD-7", lines=lines)
        assert "CMD-7" in text and "3 000" in text

    @pytest.mark.parametrize("language, label", [("fr", "Motif : "), ("darija", "Sabab: "), ("ar", "السبب: ")])
    def test_rejection_reason(self, language, label):
        reason = reason_line("Stock  insuffisant", language)
        text = render("order_rejected", language, reference="CMD-7", reason=reason)
        assert f"\n{label}Stock insuffisant" in text
        assert "CMD-7" in text

    def test_rejection_without_reason(self):
        assert reason_line(None, "fr") == reason_line("   ", "ar") == ""
        assert "Motif" not in render("order_rejected", "fr", reference="CMD-7", reason=reason_line("", "fr"))

    def test_long_reason_is_shortened(self):
        assert len(reason_line("x" * 1000, "fr")) == len("\nMotif : ") + messages.MAX_REASON_CHARS
