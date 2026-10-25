"""Tests de la conversion des médias en texte (tecpap.processing.multimodal), sans appel réseau."""

import pytest
from PIL import Image

from tecpap.config import Settings
from tecpap.extraction.prompts import IMAGE_TEXT_INSTRUCTION, TRANSCRIPTION_HINT
from tecpap.processing import multimodal
from tecpap.processing.multimodal import (
    KIND_AUDIO,
    KIND_IMAGE,
    KIND_PDF,
    KIND_TEXT,
    KIND_UNKNOWN,
    MediaProcessor,
    MediaText,
    base_mime,
    detect_media_kind,
    extension_for_mime,
    kind_from_mime,
)
from tests.fakes import FakeLLMClient


def _pdf(path, text="Bon de commande 3000 sachets"):
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(path))
    if text:
        pdf.drawString(100, 750, text)
    pdf.showPage()
    pdf.save()
    return path


def _image(path, image_format="PNG"):
    Image.new("RGB", (12, 12), "white").save(path, image_format)
    return path


@pytest.fixture
def settings(tmp_path):
    return Settings.for_tests(tmp_path)


@pytest.fixture
def llm():
    return FakeLLMClient()


@pytest.fixture
def processor(llm, settings):
    return MediaProcessor(llm, settings)


class TestPdf:
    """PDF : texte extrait avec pypdf, sans modèle de langue."""

    def test_real_pdf_is_read(self, processor, llm, tmp_path):
        result = processor.process_file(_pdf(tmp_path / "bon.pdf"))
        assert result.kind == KIND_PDF
        assert result.error is None
        assert "3000 sachets" in result.text
        assert llm.calls == []

    def test_type_comes_from_content_not_name(self, processor, tmp_path):
        result = processor.process_file(_pdf(tmp_path / "piece_jointe.bin"))
        assert result.kind == KIND_PDF
        assert "3000 sachets" in result.text

    def test_pdf_without_text(self, processor, tmp_path):
        result = processor.process_file(_pdf(tmp_path / "scan.pdf", text=None))
        assert result.kind == KIND_PDF
        assert result.text is None
        assert "texte extractible" in result.error

    def test_malformed_pdf_does_not_raise(self, processor, tmp_path):
        path = tmp_path / "abime.pdf"
        path.write_bytes(b"%PDF-1.4\n contenu tronque sans structure")
        result = processor.process_file(path)
        assert result.kind == KIND_PDF
        assert result.error

    def test_pdf_to_text_direct(self, processor, tmp_path):
        assert "3000 sachets" in processor.pdf_to_text(_pdf(tmp_path / "bon.pdf")).text


class TestImage:
    """Images : format vérifié par Pillow, texte lu par le modèle de vision."""

    def test_real_png_goes_to_the_vision_model(self, tmp_path):
        llm = FakeLLMClient(image_to_text=["Commande 2000 sachets"])
        processor = MediaProcessor(llm, Settings.for_tests(tmp_path, model_vision="vision-test"))
        path = _image(tmp_path / "photo.png")

        result = processor.process_file(path)

        assert result == MediaText(KIND_IMAGE, "Commande 2000 sachets")
        call = llm.calls_to("image_to_text")[0]
        assert call["mime_type"] == "image/png"
        assert call["model"] == "vision-test"
        assert call["instruction"] == IMAGE_TEXT_INSTRUCTION
        assert call["image_bytes"] == path.read_bytes()

    def test_mime_type_comes_from_content(self, tmp_path):
        llm = FakeLLMClient(image_to_text=["texte"])
        MediaProcessor(llm, Settings.for_tests(tmp_path)).process_file(_image(tmp_path / "photo.png", "JPEG"))
        assert llm.calls_to("image_to_text")[0]["mime_type"] == "image/jpeg"

    def test_image_without_api_key(self, tmp_path, settings):
        llm = FakeLLMClient(available=False)
        result = MediaProcessor(llm, settings).process_file(_image(tmp_path / "photo.png"))
        assert result.kind == KIND_IMAGE
        assert "OPENAI_API_KEY" in result.error
        assert llm.calls == []

    def test_vision_failure_is_reported(self, processor, tmp_path):
        result = processor.process_file(_image(tmp_path / "photo.png"))
        assert result.kind == KIND_IMAGE
        assert result.text is None
        assert result.error

    def test_image_format_outside_allowlist_is_refused(self, processor, llm, tmp_path):
        result = processor.process_file(_image(tmp_path / "photo.bmp", "BMP"))
        assert result.kind == KIND_UNKNOWN
        assert result.error
        assert llm.calls == []

    def test_fake_image_extension_is_not_trusted(self, processor, llm, tmp_path):
        path = tmp_path / "photo.jpg"
        path.write_bytes(b"MZ\x90\x00 pas une image")
        result = processor.process_file(path)
        assert result.kind == KIND_UNKNOWN
        assert llm.calls == []


class TestAudio:
    """Notes vocales : extension ou type MIME déclaré, transcription par le modèle configuré."""

    @pytest.fixture
    def audio(self, tmp_path):
        path = tmp_path / "note.ogg"
        path.write_bytes(b"OggS" + b"0" * 500)
        return path

    def test_audio_goes_to_transcription_with_configured_model(self, settings, audio):
        llm = FakeLLMClient(transcribe=["bghit 3000 sachet"])
        result = MediaProcessor(llm, settings).process_file(audio)
        assert result == MediaText(KIND_AUDIO, "bghit 3000 sachet")
        call = llm.calls_to("transcribe")[0]
        assert call["file_path"] == str(audio)
        assert call["model"] == "whisper-1"
        assert call["language"] is None
        assert call["prompt"] == TRANSCRIPTION_HINT

    def test_configured_model_and_language(self, tmp_path, audio):
        llm = FakeLLMClient(transcribe=["texte"])
        settings = Settings.for_tests(tmp_path, model_transcription="transcription-test", transcription_language="ar")
        MediaProcessor(llm, settings).process_file(audio)
        call = llm.calls_to("transcribe")[0]
        assert call["model"] == "transcription-test"
        assert call["language"] == "ar"

    def test_declared_mime_identifies_audio(self, settings, tmp_path):
        path = tmp_path / "media.bin"
        path.write_bytes(b"0" * 500)
        llm = FakeLLMClient(transcribe=["kif dima"])
        result = MediaProcessor(llm, settings).process_file(path, declared_mime="audio/ogg; codecs=opus")
        assert result.kind == KIND_AUDIO
        assert result.text == "kif dima"

    def test_too_short_audio_is_ignored(self, processor, llm, tmp_path):
        path = tmp_path / "vide.ogg"
        path.write_bytes(b"0")
        result = processor.process_file(path)
        assert result.kind == KIND_AUDIO
        assert "trop court" in result.error
        assert llm.calls == []

    def test_transcription_failure_is_reported(self, processor, audio):
        result = processor.process_file(audio)
        assert result.kind == KIND_AUDIO
        assert result.text is None
        assert result.error

    def test_audio_without_api_key(self, settings, audio):
        llm = FakeLLMClient(available=False)
        result = MediaProcessor(llm, settings).audio_to_text(audio, model="whisper-1")
        assert "OPENAI_API_KEY" in result.error
        assert llm.calls == []

    def test_audio_to_text_direct(self, settings, audio):
        llm = FakeLLMClient(transcribe=["texte"])
        MediaProcessor(llm, settings).audio_to_text(audio, model="modele", language="fr", prompt="indice")
        assert llm.calls_to("transcribe")[0] == {
            "file_path": str(audio), "model": "modele", "language": "fr", "prompt": "indice",
        }


class TestTextFiles:
    """Fichiers texte."""

    def test_utf8_text(self, processor, tmp_path):
        path = tmp_path / "commande.txt"
        path.write_text("bghit 2000 sachet\n", encoding="utf-8")
        assert processor.process_file(path) == MediaText(KIND_TEXT, "bghit 2000 sachet")

    def test_byte_order_mark_is_removed(self, processor, tmp_path):
        path = tmp_path / "commande.csv"
        path.write_bytes("﻿produit;quantite\nsachet;500".encode())
        assert processor.process_file(path).text == "produit;quantite\nsachet;500"

    def test_non_utf8_text_is_reported(self, processor, tmp_path):
        path = tmp_path / "commande.txt"
        path.write_bytes("quantité".encode("cp1252"))
        result = processor.process_file(path)
        assert result.kind == KIND_TEXT
        assert "UTF-8" in result.error

    def test_empty_text_file(self, processor, tmp_path):
        path = tmp_path / "vide.txt"
        path.write_text("  \n", encoding="utf-8")
        assert processor.process_file(path).error

    def test_binary_content_with_text_extension(self, processor, tmp_path):
        path = tmp_path / "faux.txt"
        path.write_bytes(b"abc\x00def")
        assert processor.process_file(path).error


class TestRefusals:
    """Fichiers refusés : trop gros, type inconnu, absents."""

    def test_oversized_file_is_refused(self, tmp_path):
        llm = FakeLLMClient(transcribe=["texte"])
        processor = MediaProcessor(llm, Settings.for_tests(tmp_path, max_media_bytes=100))
        path = tmp_path / "note.ogg"
        path.write_bytes(b"0" * 101)
        result = processor.process_file(path)
        assert result.text is None
        assert "volumineux" in result.error
        assert llm.calls == []

    def test_oversized_file_is_refused_by_each_conversion(self, tmp_path):
        processor = MediaProcessor(FakeLLMClient(), Settings.for_tests(tmp_path, max_media_bytes=10))
        path = _pdf(tmp_path / "bon.pdf")
        assert "volumineux" in processor.pdf_to_text(path).error

    def test_file_at_the_limit_is_accepted(self, tmp_path):
        processor = MediaProcessor(FakeLLMClient(), Settings.for_tests(tmp_path, max_media_bytes=5))
        path = tmp_path / "court.txt"
        path.write_text("12345", encoding="utf-8")
        assert processor.process_file(path).text == "12345"

    def test_unknown_type_is_refused(self, processor, llm, tmp_path):
        path = tmp_path / "archive.zip"
        path.write_bytes(b"PK\x03\x04" + b"0" * 50)
        result = processor.process_file(path)
        assert result == MediaText(KIND_UNKNOWN, None, "type de fichier non pris en charge")
        assert llm.calls == []

    def test_unknown_declared_mime_is_refused(self, processor, tmp_path):
        path = tmp_path / "media.bin"
        path.write_bytes(b"0" * 50)
        assert processor.process_file(path, declared_mime="application/zip").kind == KIND_UNKNOWN

    def test_missing_file(self, processor, tmp_path):
        result = processor.process_file(tmp_path / "absent.pdf")
        assert "introuvable" in result.error

    def test_unexpected_error_does_not_raise(self, processor, tmp_path, monkeypatch):
        def broken(*args, **kwargs):
            raise RuntimeError("panne")

        monkeypatch.setattr(multimodal, "detect_media_kind", broken)
        path = tmp_path / "commande.txt"
        path.write_text("texte", encoding="utf-8")
        result = processor.process_file(path)
        assert result == MediaText(KIND_UNKNOWN, None, multimodal.UNEXPECTED_ERROR)


class TestDetection:
    """Détection du type de fichier et des types MIME."""

    def test_detect_kinds(self, tmp_path):
        assert detect_media_kind(_pdf(tmp_path / "a.pdf")) == KIND_PDF
        assert detect_media_kind(_image(tmp_path / "b.webp", "WEBP")) == KIND_IMAGE
        (tmp_path / "c.m4a").write_bytes(b"0" * 10)
        assert detect_media_kind(tmp_path / "c.m4a") == KIND_AUDIO

    @pytest.mark.parametrize("mime, kind", [
        ("application/pdf", KIND_PDF),
        ("image/jpeg", KIND_IMAGE),
        ("IMAGE/PNG", KIND_IMAGE),
        ("audio/ogg; codecs=opus", KIND_AUDIO),
        ("audio/mpeg", KIND_AUDIO),
        ("text/plain", KIND_TEXT),
        ("image/heic", KIND_UNKNOWN),
        ("application/zip", KIND_UNKNOWN),
        ("", KIND_UNKNOWN),
        (None, KIND_UNKNOWN),
    ])
    def test_kind_from_mime(self, mime, kind):
        assert kind_from_mime(mime) == kind

    def test_extension_for_mime(self):
        assert extension_for_mime("audio/ogg; codecs=opus") == ".ogg"
        assert extension_for_mime("image/jpeg") == ".jpg"
        assert extension_for_mime("application/octet-stream") == ".bin"

    def test_base_mime(self):
        assert base_mime(" Audio/OGG; codecs=opus ") == "audio/ogg"
        assert base_mime(None) == ""

    def test_media_text_ok(self):
        assert MediaText(KIND_TEXT, "x").ok is True
        assert MediaText(KIND_TEXT, "").ok is False
        assert MediaText(KIND_TEXT, None, "erreur").ok is False


class TestTranscriptionHint:
    """Vocabulaire transmis au modèle de transcription."""

    @pytest.mark.parametrize("name", ["Salah", "Eddine", "Mohamed", "Ahmed", "Hassan", "Youssef"])
    def test_no_personal_first_name(self, name):
        assert name.lower() not in TRANSCRIPTION_HINT.lower()

    def test_business_terms_in_three_languages(self):
        for term in ("sachets", "kraft", "bghit", "kif dima", "بغيت", "درهم"):
            assert term in TRANSCRIPTION_HINT
