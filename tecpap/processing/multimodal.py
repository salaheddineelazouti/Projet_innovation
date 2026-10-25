"""Conversion en texte des fichiers reçus : PDF, images, notes vocales et fichiers texte.

Détection et conversion par type :

- PDF : signature ``%PDF-`` au début du fichier ; texte extrait avec pypdf.
- Image (JPEG, PNG, GIF, WebP) : format reconnu par Pillow d'après le contenu ; texte lu par le
  modèle de vision (``Settings.model_vision``).
- Audio : extension ou type MIME déclaré ; transcription par le modèle de transcription
  (``Settings.model_transcription``), langue ``Settings.transcription_language`` (détection
  automatique sans valeur) et vocabulaire ``TRANSCRIPTION_HINT``.
- Texte (.txt, .csv) : extension ou type MIME déclaré ; lecture en UTF-8.

Le type MIME est le type de contenu annoncé par l'expéditeur ou par Twilio (par exemple
``audio/ogg``). Pour les PDF et les images, le type est vérifié d'après le contenu du fichier :
un fichier nommé « bon.pdf » qui n'est pas un PDF n'est pas lu comme tel. Les formats audio
n'ont pas de signature commune simple à vérifier, d'où le recours à l'extension et au type
MIME déclaré. Tout autre type est refusé, de même qu'un fichier plus gros que
``Settings.max_media_bytes``.

Aucune méthode ne lève d'exception : un échec est décrit dans le champ ``error`` de
:class:`MediaText`.
"""

from __future__ import annotations

import functools
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pypdf
from PIL import Image

from tecpap.extraction.prompts import IMAGE_TEXT_INSTRUCTION, TRANSCRIPTION_HINT

if TYPE_CHECKING:
    from tecpap.config import Settings
    from tecpap.llm.client import LLMClient

logger = logging.getLogger(__name__)

KIND_PDF = "pdf"
KIND_IMAGE = "image"
KIND_AUDIO = "audio"
KIND_TEXT = "text"
KIND_UNKNOWN = "unknown"

# Formats d'image acceptés par le modèle de vision : nom du format Pillow -> type MIME.
IMAGE_FORMATS: dict[str, str] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}

# Extensions des formats audio acceptés par le service de transcription.
AUDIO_EXTENSIONS = frozenset({".flac", ".m4a", ".mp3", ".mp4", ".mpeg", ".mpga", ".oga", ".ogg", ".wav", ".webm"})

TEXT_EXTENSIONS = frozenset({".txt", ".csv"})

# Type MIME déclaré -> extension du fichier enregistré. L'extension indique son format au
# service de transcription : elle doit correspondre au contenu.
MIME_EXTENSIONS: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "audio/ogg": ".ogg",
    "audio/opus": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/m4a": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/webm": ".webm",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "text/plain": ".txt",
    "text/csv": ".csv",
}

AUDIO_MIME_TYPES = frozenset(mime for mime in MIME_EXTENSIONS if mime.startswith("audio/"))
TEXT_MIME_TYPES = frozenset({"text/plain", "text/csv"})

MIN_AUDIO_BYTES = 100
"""Taille en dessous de laquelle un fichier audio est considéré comme vide."""

MAX_PDF_PAGES = 100
"""Nombre maximal de pages lues dans un PDF ; un bon de commande en compte rarement plus de trois."""

UNEXPECTED_ERROR = "erreur inattendue pendant la lecture du fichier"


@dataclass(frozen=True)
class MediaText:
    """Résultat de la conversion d'un fichier en texte.

    Attributes:
        kind: type détecté (``pdf``, ``image``, ``audio``, ``text`` ou ``unknown``).
        text: texte obtenu, ou ``None`` en cas d'échec.
        error: description de l'échec en français, ou ``None``.
    """

    kind: str
    text: str | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Vrai quand un texte non vide a été obtenu sans erreur."""
        return self.error is None and bool(self.text)


def base_mime(declared_mime: str | None) -> str:
    """Type MIME sans paramètres et en minuscules (``"audio/ogg; codecs=opus"`` -> ``"audio/ogg"``)."""
    return (declared_mime or "").split(";", 1)[0].strip().lower()


def extension_for_mime(declared_mime: str | None) -> str:
    """Extension de fichier correspondant à un type MIME accepté, ``".bin"`` sinon."""
    return MIME_EXTENSIONS.get(base_mime(declared_mime), ".bin")


def kind_from_mime(declared_mime: str | None) -> str:
    """Type de média annoncé par un type MIME, avant tout examen du contenu."""
    mime = base_mime(declared_mime)
    if mime == "application/pdf":
        return KIND_PDF
    if mime in IMAGE_FORMATS.values():
        return KIND_IMAGE
    if mime in AUDIO_MIME_TYPES:
        return KIND_AUDIO
    if mime in TEXT_MIME_TYPES:
        return KIND_TEXT
    return KIND_UNKNOWN


def _image_mime(path: str | os.PathLike[str]) -> str | None:
    """Type MIME d'une image d'un format accepté, d'après son contenu ; ``None`` sinon."""
    try:
        # Seuls les formats acceptés sont essayés : Pillow reconnaît des dizaines de formats,
        # dont certains sans signature fiable qui pourraient être confondus avec un autre fichier.
        with Image.open(path, formats=list(IMAGE_FORMATS)) as image:
            return IMAGE_FORMATS.get(image.format or "")
    except Exception:
        return None


def detect_media_kind(path: str | os.PathLike[str], declared_mime: str | None = None) -> str:
    """Détermine le type d'un fichier : contenu pour les PDF et les images, extension ou MIME sinon."""
    with open(path, "rb") as f:
        head = f.read(1024)
    # La norme PDF tolère quelques octets avant la signature ; les lecteurs la cherchent dans
    # le premier kilo-octet.
    if b"%PDF-" in head:
        return KIND_PDF
    if _image_mime(path):
        return KIND_IMAGE

    extension = Path(path).suffix.lower()
    mime = base_mime(declared_mime)
    if extension in AUDIO_EXTENSIONS or mime in AUDIO_MIME_TYPES:
        return KIND_AUDIO
    if extension in TEXT_EXTENSIONS or mime in TEXT_MIME_TYPES:
        return KIND_TEXT
    return KIND_UNKNOWN


def _never_raises(kind: str) -> Callable[[Callable[..., MediaText]], Callable[..., MediaText]]:
    """Transforme toute exception d'une méthode de conversion en :class:`MediaText` d'erreur."""
    def decorator(method: Callable[..., MediaText]) -> Callable[..., MediaText]:
        @functools.wraps(method)
        def wrapper(self: MediaProcessor, path: Any, *args: Any, **kwargs: Any) -> MediaText:
            try:
                return method(self, path, *args, **kwargs)
            except Exception:
                logger.exception("Lecture du fichier impossible : %s", _name(path))
                return MediaText(kind, None, UNEXPECTED_ERROR)
        return wrapper
    return decorator


def _name(path: Any) -> str:
    return os.path.basename(str(path))


class MediaProcessor:
    """Conversion en texte des pièces jointes et des médias WhatsApp.

    Args:
        llm: client des modèles de langue, utilisé pour les images et l'audio ; sans client
            disponible, seuls les PDF et les fichiers texte sont lus.
        settings: configuration (taille maximale, modèles de vision et de transcription,
            langue de transcription).
    """

    def __init__(self, llm: LLMClient | None, settings: Settings):
        self.llm = llm
        self.settings = settings

    @property
    def llm_available(self) -> bool:
        """Vrai quand le client des modèles de langue peut être appelé."""
        return self.llm is not None and self.llm.available

    # ------------------------------------------------------------------ point d'entrée

    @_never_raises(KIND_UNKNOWN)
    def process_file(self, path: str | os.PathLike[str], declared_mime: str | None = None) -> MediaText:
        """Détecte le type de ``path`` et le convertit en texte.

        Args:
            path: fichier à lire.
            declared_mime: type MIME annoncé par l'expéditeur, s'il est connu ; il ne sert
                que pour l'audio et le texte, le contenu faisant foi pour les PDF et les images.
        """
        refused = self._refuse_if_invalid(path, KIND_UNKNOWN)
        if refused:
            return refused

        kind = detect_media_kind(path, declared_mime)
        if kind == KIND_PDF:
            return self.pdf_to_text(path)
        if kind == KIND_IMAGE:
            return self.image_to_text(path)
        if kind == KIND_AUDIO:
            return self.audio_to_text(
                path,
                model=self.settings.model_transcription,
                language=self.settings.transcription_language,
                prompt=TRANSCRIPTION_HINT,
            )
        if kind == KIND_TEXT:
            return self.text_file(path)

        logger.info("Type de fichier non pris en charge : %s (type MIME déclaré : %s)",
                    _name(path), base_mime(declared_mime) or "aucun")
        return MediaText(KIND_UNKNOWN, None, "type de fichier non pris en charge")

    # ------------------------------------------------------------------ conversions

    @_never_raises(KIND_PDF)
    def pdf_to_text(self, path: str | os.PathLike[str]) -> MediaText:
        """Extrait le texte d'un PDF avec pypdf (au plus :data:`MAX_PDF_PAGES` pages)."""
        refused = self._refuse_if_invalid(path, KIND_PDF)
        if refused:
            return refused
        try:
            reader = pypdf.PdfReader(path)
            # Un PDF peut être chiffré sans mot de passe d'ouverture (impression ou copie
            # restreintes seulement) : il se lit avec un mot de passe vide.
            if reader.is_encrypted and not reader.decrypt(""):
                return MediaText(KIND_PDF, None, "PDF protégé par un mot de passe")
            pages = reader.pages
            if len(pages) > MAX_PDF_PAGES:
                logger.warning("PDF %s : %d pages, seules les %d premières sont lues",
                               _name(path), len(pages), MAX_PDF_PAGES)
            text = "\n".join(pages[index].extract_text() or "" for index in range(min(len(pages), MAX_PDF_PAGES)))
        except Exception:
            # Un PDF reçu d'un client peut être malformé de multiples façons : il est alors ignoré.
            logger.warning("Lecture du PDF impossible : %s", _name(path), exc_info=True)
            return MediaText(KIND_PDF, None, "PDF illisible")

        text = text.strip()
        if not text:
            return MediaText(KIND_PDF, None, "le PDF ne contient pas de texte extractible (document numérisé ?)")
        logger.info("PDF lu : %s", _name(path))
        return MediaText(KIND_PDF, text)

    @_never_raises(KIND_IMAGE)
    def image_to_text(self, path: str | os.PathLike[str]) -> MediaText:
        """Fait lire le texte d'une image par le modèle de vision (``Settings.model_vision``)."""
        refused = self._refuse_if_invalid(path, KIND_IMAGE)
        if refused:
            return refused
        mime = _image_mime(path)
        if not mime:
            return MediaText(KIND_IMAGE, None, "image illisible ou de format non pris en charge")
        if not self.llm_available:
            logger.warning("Lecture d'image impossible : clé OPENAI_API_KEY absente")
            return MediaText(KIND_IMAGE, None, "lecture d'image indisponible : clé OPENAI_API_KEY absente")

        image_bytes = Path(path).read_bytes()
        text = self.llm.image_to_text(
            image_bytes, mime, model=self.settings.model_vision, instruction=IMAGE_TEXT_INSTRUCTION,
        )
        if not text:
            return MediaText(KIND_IMAGE, None, "lecture de l'image en échec ou image sans texte")
        logger.info("Image lue : %s", _name(path))
        return MediaText(KIND_IMAGE, text)

    @_never_raises(KIND_AUDIO)
    def audio_to_text(self, path: str | os.PathLike[str], *, model: str, language: str | None = None,
                      prompt: str | None = None) -> MediaText:
        """Transcrit un fichier audio.

        Args:
            model: modèle de transcription.
            language: code ISO 639-1 de la langue parlée ; sans valeur, le modèle détecte la
                langue lui-même (une note vocale peut mêler darija, arabe et français).
            prompt: vocabulaire qui oriente la transcription (voir ``TRANSCRIPTION_HINT``).
        """
        refused = self._refuse_if_invalid(path, KIND_AUDIO)
        if refused:
            return refused
        size = os.path.getsize(path)
        if size < MIN_AUDIO_BYTES:
            logger.warning("Fichier audio vide ou trop court (%d octets) : %s", size, _name(path))
            return MediaText(KIND_AUDIO, None, "fichier audio vide ou trop court")
        if not self.llm_available:
            logger.warning("Transcription impossible : clé OPENAI_API_KEY absente")
            return MediaText(KIND_AUDIO, None, "transcription indisponible : clé OPENAI_API_KEY absente")

        text = self.llm.transcribe(str(path), model=model, language=language, prompt=prompt)
        if not text:
            return MediaText(KIND_AUDIO, None, "transcription en échec ou vide")
        logger.info("Audio transcrit : %s", _name(path))
        return MediaText(KIND_AUDIO, text)

    @_never_raises(KIND_TEXT)
    def text_file(self, path: str | os.PathLike[str]) -> MediaText:
        """Lit un fichier texte encodé en UTF-8 (avec ou sans marque d'ordre des octets)."""
        refused = self._refuse_if_invalid(path, KIND_TEXT)
        if refused:
            return refused
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            logger.warning("Fichier texte illisible (encodage autre que UTF-8) : %s", _name(path))
            return MediaText(KIND_TEXT, None, "fichier texte illisible : encodage UTF-8 attendu")
        if "\x00" in text:
            return MediaText(KIND_TEXT, None, "fichier binaire présenté comme un fichier texte")
        text = text.strip()
        if not text:
            return MediaText(KIND_TEXT, None, "fichier texte vide")
        return MediaText(KIND_TEXT, text)

    # ------------------------------------------------------------------ contrôles

    def _refuse_if_invalid(self, path: str | os.PathLike[str], kind: str) -> MediaText | None:
        """Refuse un fichier absent ou plus gros que ``Settings.max_media_bytes``."""
        if not os.path.isfile(path):
            logger.warning("Fichier introuvable : %s", _name(path))
            return MediaText(kind, None, "fichier introuvable")
        size = os.path.getsize(path)
        if size > self.settings.max_media_bytes:
            logger.warning("Fichier refusé : %s fait %d octets, limite %d",
                           _name(path), size, self.settings.max_media_bytes)
            return MediaText(kind, None, f"fichier trop volumineux (limite {self.settings.max_media_bytes} octets)")
        return None
