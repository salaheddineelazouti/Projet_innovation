"""Repérage et masquage des numéros de téléphone et des adresses email (tecpap.security.redaction)."""

from __future__ import annotations

import pytest

from tecpap.security.redaction import (
    EMAIL_PLACEHOLDER,
    MASK_CHAR,
    PHONE_PLACEHOLDER,
    SensitiveMatch,
    _replace,
    canonical_phone,
    find_emails,
    find_phone_numbers,
    mask_email,
    mask_identifier,
    mask_phone,
    mask_sender,
    normalize_email,
    normalize_phone_set,
    redact_contacts,
    redact_emails,
    redact_phones,
)


class TestCanonicalPhone:
    """Forme E.164 des numéros."""

    @pytest.mark.parametrize("raw, expected", [
        ("+212612345678", "+212612345678"),
        ("+212 6 12 34 56 78", "+212612345678"),
        ("+212-6-12-34-56-78", "+212612345678"),
        ("+212.6.12.34.56.78", "+212612345678"),
        ("0612345678", "+212612345678"),
        ("06 12 34 56 78", "+212612345678"),
        ("06.12.34.56.78", "+212612345678"),
        ("06-12-34-56-78", "+212612345678"),
        ("0712345678", "+212712345678"),
        ("05 22 86 56 83", "+212522865683"),
        ("+212 (0)5 22 86 56 83", "+212522865683"),
        ("+212 06 12 34 56 78", "+212612345678"),
        ("(+212) 6 12 34 56 78", "+212612345678"),
        ("00212 6 12 34 56 78", "+212612345678"),
        ("212612345678", "+212612345678"),
        ("whatsapp:+212612345678", "+212612345678"),
        ("WhatsApp: +212612345678", "+212612345678"),
        ("tel:+33612345678", "+33612345678"),
        ("+33 6 12 34 56 78", "+33612345678"),
        ("+1 (415) 523-8886", "+14155238886"),
        ("whatsapp:+14155238886", "+14155238886"),
        ("+44 20 7946 0958", "+442079460958"),
        ("٠٦١٢٣٤٥٦٧٨", "+212612345678"),
        ("۰۶۱۲۳۴۵۶۷۸", "+212612345678"),
        ("０６１２３４５６７８", "+212612345678"),
    ])
    def test_valid_formats(self, raw, expected):
        assert canonical_phone(raw) == expected

    @pytest.mark.parametrize("raw", [
        None, "", "   ", "abc", "10 000", "1500000", "15/11/2026", "2026-10-20", "BC-2026-0417",
        "123456789", "+212 6 12 34", "+2126123456789", "+10 000 000", "0012345678", "+0612345678",
        "1234567890", "7 600,00",
    ])
    def test_values_that_are_not_phone_numbers(self, raw):
        assert canonical_phone(raw) is None

    def test_normalize_phone_set_ignores_invalid_values(self):
        assert normalize_phone_set(["0612345678", "whatsapp:+212612345678", "", None, "abc"]) == {"+212612345678"}
        assert normalize_phone_set(None) == frozenset()


class TestFindPhoneNumbers:
    """Repérage des numéros dans un texte."""

    def test_number_with_spaces_in_a_sentence(self):
        matches = find_phone_numbers("Appelez le 06 12 34 56 78 svp")
        assert [(m.value, m.normalized) for m in matches] == [("06 12 34 56 78", "+212612345678")]
        assert matches[0].start == len("Appelez le ")

    def test_landline_with_trunk_prefix_in_parentheses(self):
        [match] = find_phone_numbers("Téléphone : +212 (0)5 22 86 56 83.")
        assert match.value == "+212 (0)5 22 86 56 83"
        assert match.normalized == "+212522865683"

    @pytest.mark.parametrize("text, expected", [
        ("tel 0612345678 10 000 sachets", "0612345678"),
        ("2000 0612345678", "0612345678"),
        ("06 12 34 56 78 90", "06 12 34 56 78"),
    ])
    def test_number_next_to_a_quantity_is_separated(self, text, expected):
        assert [m.value for m in find_phone_numbers(text)] == [expected]

    def test_several_numbers(self):
        text = "Moi : 0612345678, mon associé : +212 7 11 22 33 44 et l'usine au 05.22.86.56.83"
        assert [m.normalized for m in find_phone_numbers(text)] == ["+212612345678", "+212711223344", "+212522865683"]

    def test_arabic_indic_digits_keep_their_positions(self):
        text = "رقمي ٠٦١٢٣٤٥٦٧٨ شكرا"
        [match] = find_phone_numbers(text)
        assert text[match.start:match.end] == "٠٦١٢٣٤٥٦٧٨"

    def test_plus_sign_starts_a_new_number(self):
        assert [m.value for m in find_phone_numbers("2000 +212612345678")] == ["+212612345678"]
        assert find_phone_numbers("0612+345678") == []

    def test_number_inside_a_url(self):
        assert [m.normalized for m in find_phone_numbers("wa.me/212612345678")] == ["+212612345678"]

    @pytest.mark.parametrize("text", [
        "BC-2026-0417 du 15/11/2026 pour 10 000 unités à 7 600,00",
        "Quantité : 10 000 - 20 000 - 30 000",
        "Code client CL00231, article KB100L28MON, format 18x8x30",
        "prix 0,45 MAD, total 1 500 000",
        "Commande n° 2026/118 du 2026-10-20 à 06:30",
        "0612345678@exemple.ma",
        "",
    ])
    def test_ordinary_numbers_are_not_detected(self, text):
        assert find_phone_numbers(text) == []

    def test_none(self):
        assert find_phone_numbers(None) == []

    def test_long_digit_runs_are_processed_quickly(self):
        assert find_phone_numbers("0612 " * 2000) is not None
        assert find_phone_numbers("1" * 20000) == []


class TestRedactPhones:
    """Masquage des numéros."""

    def test_unknown_number_is_replaced(self):
        assert redact_phones("Appelez le 06 12 34 56 78.") == f"Appelez le {PHONE_PLACEHOLDER}."

    @pytest.mark.parametrize("allowed", ["0612345678", "+212612345678", "whatsapp:+212612345678", "+212 6 12 34 56 78"])
    def test_allowed_number_is_kept_whatever_its_form(self, allowed):
        text = "Votre numéro 06 12 34 56 78 est enregistré, pas le 0699887766."
        assert redact_phones(text, [allowed]) == f"Votre numéro 06 12 34 56 78 est enregistré, pas le {PHONE_PLACEHOLDER}."

    def test_custom_placeholder(self):
        assert redact_phones("tel 0612345678", placeholder="<TELEPHONE>") == "tel <TELEPHONE>"

    def test_text_without_number_is_unchanged(self):
        text = "Commande BC-2026-0417 : 10 000 sachets livrés le 15/11/2026."
        assert redact_phones(text) == text

    def test_empty_values(self):
        assert redact_phones(None) == ""
        assert redact_phones("") == ""
        assert redact_phones("x", None) == "x"


class TestEmails:
    """Repérage, normalisation et masquage des adresses email."""

    @pytest.mark.parametrize("raw, expected", [
        ("H.Bennani@Exemple.MA", "h.bennani@exemple.ma"),
        ("mailto:info@tecpap.net", "info@tecpap.net"),
        ("  contact@exemple.ma ", "contact@exemple.ma"),
        ("ali [at] exemple [dot] ma", "ali@exemple.ma"),
        ("ali (arobase) exemple (point) ma", "ali@exemple.ma"),
        (None, ""),
    ])
    def test_normalize_email(self, raw, expected):
        assert normalize_email(raw) == expected

    def test_find_emails_in_text(self):
        text = "Écrivez à h.bennani@exemple.ma ou commandes.atlasnakhla@exemple.ma."
        assert [m.normalized for m in find_emails(text)] == ["h.bennani@exemple.ma", "commandes.atlasnakhla@exemple.ma"]

    def test_find_obfuscated_email(self):
        [match] = find_emails("contact : ali [at] exemple [dot] ma merci")
        assert match.normalized == "ali@exemple.ma"

    def test_obfuscated_form_overlapping_a_classic_address_is_not_counted_twice(self):
        matches = find_emails("ali@exemple.ma (at) site (dot) ma")
        assert [m.normalized for m in matches] == ["ali@exemple.ma"]

    def test_website_is_not_an_email(self):
        assert find_emails("Site web : www.tecpap.ma") == []

    def test_redact_emails_with_allowed_address(self):
        text = "Contact : info@tecpap.net ou h.bennani@exemple.ma"
        assert redact_emails(text, ["INFO@TECPAP.NET"]) == f"Contact : info@tecpap.net ou {EMAIL_PLACEHOLDER}"

    def test_redact_emails_empty_values(self):
        assert redact_emails(None) == ""
        assert redact_emails("sans adresse") == "sans adresse"


class TestRedactContacts:
    """Masquage combiné des emails puis des numéros."""

    def test_counts_and_order(self):
        text = "Contact : h.bennani@exemple.ma, 06 12 34 56 78, 0612345678@exemple.ma, info@tecpap.net"
        result = redact_contacts(text, allowed_emails=["info@tecpap.net"])
        assert result.text == (
            f"Contact : {EMAIL_PLACEHOLDER}, {PHONE_PLACEHOLDER}, {EMAIL_PLACEHOLDER}, info@tecpap.net"
        )
        assert (result.emails, result.phones, result.total) == (2, 1, 3)

    def test_allowed_phone(self):
        result = redact_contacts("Votre numéro : +212612345678", allowed_phones=["whatsapp:+212612345678"])
        assert result.text == "Votre numéro : +212612345678"
        assert result.total == 0

    def test_empty_text(self):
        assert redact_contacts(None).text == ""
        assert redact_contacts("").total == 0

    def test_overlapping_matches_are_replaced_once(self):
        text = "appel 0612345678 fin"
        overlapping = [SensitiveMatch(6, 16, "0612345678", "+212612345678"), SensitiveMatch(8, 12, "1234", "x")]
        assert _replace(text, overlapping, "#") == "appel # fin"


class TestMasking:
    """Masquage partiel pour les journaux."""

    @pytest.mark.parametrize("raw, expected", [
        ("+212612345678", "+2126•••••678"),
        ("whatsapp:+212612345678", "+2126•••••678"),
        ("06 12 34 56 78", "+2126•••••678"),
        ("+33612345678", "+3361••••678"),
        ("12345678", "12••••78"),
        ("12345", "•••45"),
        ("7", "•"),
        ("abc", MASK_CHAR * 3),
        ("", ""),
        (None, ""),
    ])
    def test_mask_phone(self, raw, expected):
        assert mask_phone(raw) == expected

    def test_mask_phone_hides_the_middle_digits(self):
        masked = mask_phone("+212612345678")
        assert "12345" not in masked
        assert masked.count(MASK_CHAR) == 5

    @pytest.mark.parametrize("raw, expected", [
        ("h.bennani@exemple.ma", "h•••@exemple.ma"),
        ("A@Exemple.ma", "a•••@exemple.ma"),
        ("pas-une-adresse", "pa•••"),
        (None, ""),
    ])
    def test_mask_email(self, raw, expected):
        assert mask_email(raw) == expected

    @pytest.mark.parametrize("raw, expected", [
        ("whatsapp:+212612345678", "+2126•••••678"),
        ("h.bennani@exemple.ma", "h•••@exemple.ma"),
        ("tableau_de_bord", "ta•••"),
        ("abc", "•••"),
        ("", ""),
        (None, ""),
    ])
    def test_mask_sender(self, raw, expected):
        assert mask_sender(raw) == expected

    def test_mask_identifier(self):
        assert mask_identifier("inconnu") == "in•••"
        assert mask_identifier("ab") == "•••"
        assert mask_identifier(None) == ""
