"""Tests de la mise en forme française des nombres, des montants et des dates (tecpap.formatting)."""

from __future__ import annotations

import logging
import unicodedata
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tecpap import formatting
from tecpap.formatting import (
    DECIMAL_SEPARATOR,
    MISSING_VALUE,
    THOUSANDS_SEPARATOR,
    UNIT_SEPARATOR,
    format_amount,
    format_date,
    format_datetime,
    format_number,
    format_quantity,
    parse_timestamp,
    resolve_timezone,
)

NNBSP = " "
NBSP = " "


class TestSeparators:
    def test_thousands_separator_is_the_narrow_no_break_space(self):
        assert THOUSANDS_SEPARATOR == NNBSP
        assert DECIMAL_SEPARATOR == ","
        assert UNIT_SEPARATOR == NBSP

    @pytest.mark.parametrize("character", [THOUSANDS_SEPARATOR, DECIMAL_SEPARATOR])
    def test_separators_stay_inside_a_number_in_right_to_left_text(self, character):
        # Classe « CS » (séparateur numérique commun) de l'algorithme bidirectionnel Unicode :
        # entre deux chiffres, le caractère fait partie du nombre (règle W4).
        assert unicodedata.bidirectional(character) == "CS"

    def test_an_ordinary_space_would_split_the_number(self):
        assert unicodedata.bidirectional(" ") == "WS"

    @pytest.mark.parametrize("language", ["fr", "darija", "ar", "darija_latin", "darija_ar", "en", None, 3])
    def test_every_language_uses_a_separator_valid_right_to_left(self, language):
        text = format_quantity(10000, language=language)
        assert text == f"10{NNBSP}000"
        assert " " not in text


class TestFormatNumber:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, "0"),
            (7, "7"),
            (999, "999"),
            (1000, f"1{NNBSP}000"),
            (10000, f"10{NNBSP}000"),
            (1234567, f"1{NNBSP}234{NNBSP}567"),
            (-2500, f"-2{NNBSP}500"),
            (5000.0, f"5{NNBSP}000"),
            (2.5, "2,5"),
            (1234.5, f"1{NNBSP}234,5"),
            (0.125, "0,13"),
            (Decimal("12.30"), "12,3"),
            ("10000", f"10{NNBSP}000"),
            ("2.5", "2,5"),
            ("2,5", "2,5"),
            (" 3000 ", f"3{NNBSP}000"),
        ],
    )
    def test_french_format(self, value, expected):
        assert format_number(value) == expected

    def test_minimum_decimals(self):
        assert format_number(12, min_decimals=2) == "12,00"
        assert format_number(12.5, min_decimals=2) == "12,50"

    def test_unlimited_decimals(self):
        assert format_number(1.23456, decimals=None) == "1,23456"

    def test_rounding_to_zero_does_not_keep_the_sign(self):
        assert format_number(-0.001) == "0"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_missing_values(self, value):
        assert format_number(value) == MISSING_VALUE

    def test_very_large_numbers_are_grouped_without_error(self):
        assert format_number(10**30) == NNBSP.join(["1"] + ["000"] * 10)
        assert format_number(1e30) == NNBSP.join(["1"] + ["000"] * 10)

    @pytest.mark.parametrize("value", ["1e999999", 1.5e300, 10**101])
    def test_numbers_beyond_any_realistic_amount_are_shown_as_given(self, value):
        assert format_number(value) == str(value)
        assert format_amount(value).startswith(str(value))

    def test_extremely_small_numbers_round_to_zero(self):
        assert format_number("1e-999999") == "0"
        assert format_amount(5e-324) == f"0,00{NBSP}MAD"
        assert format_number("1e-999999", decimals=None) == "1e-999999"

    @pytest.mark.parametrize("value", ["beaucoup", "10 000", "5000 pièces", float("nan"), float("inf"), True])
    def test_values_that_are_not_numbers_are_shown_as_given(self, value):
        assert format_number(value) == str(value)


class TestFormatQuantity:
    def test_integer_without_decimal_part(self):
        assert format_quantity(5000.0) == f"5{NNBSP}000"

    def test_unit_follows_a_no_break_space(self):
        assert format_quantity(5000, "pièces") == f"5{NNBSP}000{NBSP}pièces"
        assert format_quantity(2.5, "kg") == f"2,5{NBSP}kg"

    def test_unit_is_written_as_given(self):
        assert format_quantity(20, "US") == f"20{NBSP}US"

    @pytest.mark.parametrize("unit", [None, "", "  "])
    def test_no_unit(self, unit):
        assert format_quantity(300, unit) == "300"

    def test_missing_quantity_has_no_unit(self):
        assert format_quantity(None, "pièces") == MISSING_VALUE

    def test_arabic_quantity(self):
        assert format_quantity(10000, "قطعة", "ar") == f"10{NNBSP}000{NBSP}قطعة"

    def test_decimals_parameter(self):
        assert format_quantity(1.23456, decimals=3) == "1,235"


class TestFormatAmount:
    def test_two_decimals_and_currency(self):
        assert format_amount(1234.5) == f"1{NNBSP}234,50{NBSP}MAD"
        assert format_amount(12) == f"12,00{NBSP}MAD"

    def test_other_currency(self):
        assert format_amount("99.999", "EUR") == f"100,00{NBSP}EUR"

    @pytest.mark.parametrize("currency", [None, ""])
    def test_without_currency(self, currency):
        assert format_amount(15, currency) == "15,00"

    def test_missing_amount_has_no_currency(self):
        assert format_amount(None) == MISSING_VALUE

    def test_text_amount_is_shown_as_given(self):
        assert format_amount("sur devis") == "sur devis"

    def test_negative_amount(self):
        assert format_amount(-1500) == f"-1{NNBSP}500,00{NBSP}MAD"


class TestParseTimestamp:
    @pytest.mark.parametrize(
        "value",
        [
            "2026-10-25 14:03:00",
            "2026-10-25T14:03:00",
            "2026-10-25T14:03:00.123456",
            "2026-10-25T14:03:00Z",
            "2026-10-25T15:03:00+01:00",
            datetime(2026, 10, 25, 14, 3),
            datetime(2026, 10, 25, 14, 3, tzinfo=timezone.utc),
        ],
    )
    def test_moments_are_returned_in_utc(self, value):
        moment = parse_timestamp(value)
        assert moment.tzinfo is not None
        assert moment.utcoffset() == timedelta(0)
        assert (moment.year, moment.month, moment.day, moment.hour, moment.minute) == (2026, 10, 25, 14, 3)

    def test_date_only(self):
        assert parse_timestamp("2026-10-25") == date(2026, 10, 25)
        assert parse_timestamp(date(2026, 10, 25)) == date(2026, 10, 25)

    @pytest.mark.parametrize("value", [None, "", "demain", "25/10/2026", 12])
    def test_unknown_values(self, value):
        assert parse_timestamp(value) is None


class TestFormatDatetime:
    def test_converted_to_casablanca(self):
        # En juillet 2025, Casablanca était à UTC+1 (données historiques de la base IANA).
        assert format_datetime("2025-07-15 14:03:00") == "15/07/2025 15:03"

    def test_conversion_can_change_the_day(self):
        assert format_datetime("2025-07-15T23:30:00Z") == "16/07/2025 00:30"

    def test_without_time(self):
        assert format_datetime("2025-07-15T23:30:00Z", with_time=False) == "16/07/2025"
        assert format_date("2025-07-15 08:00:00") == "15/07/2025"

    def test_other_time_zone(self):
        assert format_datetime("2025-07-15 14:03:00", "Europe/Paris") == "15/07/2025 16:03"
        assert format_datetime("2025-07-15 14:03:00", "UTC") == "15/07/2025 14:03"

    def test_datetime_objects(self):
        assert format_datetime(datetime(2025, 7, 15, 14, 3)) == "15/07/2025 15:03"
        paris = timezone(timedelta(hours=2))
        assert format_datetime(datetime(2025, 7, 1, 12, 0, tzinfo=paris)) == "01/07/2025 11:00"

    def test_date_only_is_not_shifted(self):
        assert format_datetime("2026-10-25") == "25/10/2026"
        assert format_datetime(date(2026, 10, 25)) == "25/10/2026"

    @pytest.mark.parametrize("value", [None, "", "  "])
    def test_missing_values(self, value):
        assert format_datetime(value) == MISSING_VALUE

    def test_unknown_text_is_shown_as_given(self):
        assert format_datetime("la semaine prochaine") == "la semaine prochaine"


class TestTimezoneFallback:
    def setup_method(self):
        resolve_timezone.cache_clear()

    def teardown_method(self):
        resolve_timezone.cache_clear()

    def test_unknown_time_zone_falls_back_to_utc(self, caplog):
        with caplog.at_level(logging.WARNING, logger="tecpap.formatting"):
            assert resolve_timezone("Mars/Olympus_Mons") is timezone.utc
        assert "UTC" in caplog.text

    def test_missing_time_zone_data_falls_back_to_utc(self, monkeypatch):
        def missing(name):
            raise formatting.ZoneInfoNotFoundError(name)

        monkeypatch.setattr(formatting, "ZoneInfo", missing)
        assert format_datetime("2025-07-15 14:03:00") == "15/07/2025 14:03"

    @pytest.mark.parametrize("name", [None, "", "  "])
    def test_empty_name_gives_casablanca(self, name):
        assert format_datetime("2025-07-15 14:03:00", name) == "15/07/2025 15:03"

    def test_warning_is_logged_once_per_name(self, caplog):
        with caplog.at_level(logging.WARNING, logger="tecpap.formatting"):
            resolve_timezone("Nulle/Part")
            resolve_timezone("Nulle/Part")
        assert len([record for record in caplog.records if "Nulle/Part" in record.getMessage()]) == 1
