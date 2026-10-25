"""Tests de la normalisation des textes et du calcul des plongements vectoriels."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from tecpap.config import Settings
from tecpap.rag.embeddings import (
    DEFAULT_DIM,
    Embedder,
    EmbeddingResult,
    LocalHashEmbedder,
    OpenAIEmbedder,
    blob_to_vector,
    normalize_rows,
    text_features,
    vector_to_blob,
)
from tecpap.rag.text import (
    STOP_WORDS,
    light_stem,
    normalize_text,
    rewrite_arabizi,
    strip_diacritics,
    tokenize,
)
from tests.fakes import FakeLLMClient, unavailable_llm

REPO_ROOT = Path(__file__).resolve().parent.parent


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


# ---------------------------------------------------------------- normalisation et découpage


class TestNormalizeText:
    def test_lower_case_and_french_accents(self):
        assert normalize_text("Écru, poignées TORSADÉES, ça") == "ecru, poignees torsadees, ca"

    def test_compatibility_forms(self):
        # Chiffres pleine largeur et ligature « ﬁ » ramenés à leur forme simple.
        assert normalize_text("ＫＢ１００ ﬁn") == "kb100 fin"

    @pytest.mark.parametrize("variant, expected", [
        ("أكياس", "اكياس"), ("إطار", "اطار"), ("آلاف", "الاف"), ("ٱلورق", "الورق"),
        ("مستشفى", "مستشفي"), ("طلبية", "طلبيه"), ("مؤسسة", "موسسه"), ("جديدة", "جديده"),
        ("شيئ", "شيي"), ("کیس", "كيس"),
    ])
    def test_arabic_letter_variants(self, variant, expected):
        assert normalize_text(variant) == expected

    def test_arabic_diacritics_and_tatweel(self):
        assert normalize_text("كَـــيْسٌ وَرَقِيّ") == "كيس ورقي"

    def test_arabic_indic_digits(self):
        assert normalize_text("٥٠٠٠ ساشي و ۱۲۳") == "5000 ساشي و 123"

    def test_empty_text(self):
        assert normalize_text("") == ""
        assert tokenize("") == []

    def test_strip_diacritics_keeps_base_letters(self):
        assert strip_diacritics("ñ ü ç é") == "n u c e"


class TestArabizi:
    @pytest.mark.parametrize("word, expected", [
        ("b7al", "bhal"), ("l3ada", "laada"), ("9ahwi", "qahwi"), ("ch7al", "chhal"), ("m3a", "maa"),
        ("5dma", "khdma"), ("8ali", "ghali"), ("2ana", "aana"),
    ])
    def test_digits_become_letters(self, word, expected):
        assert rewrite_arabizi(word) == expected

    @pytest.mark.parametrize("word", ["kb100l28", "ke80l25", "80g", "5000", "a4", "m2", "x3", "covid19", "sachet",
                                      "l28"])
    def test_codes_quantities_and_plain_words_are_kept(self, word):
        assert rewrite_arabizi(word) == word

    def test_arabizi_and_latin_spellings_match(self):
        assert tokenize("b7al dima") == tokenize("bhal dima")


class TestTokenize:
    def test_stop_words_and_single_letters_removed(self):
        assert tokenize("Quel est le prix d'un sac ?") == ["pri", "sac"]
        assert tokenize("wach 3ndkom sachiyat ?") == ["andkom", "sachiyat"]
        assert tokenize("السلام عليكم، واش عندكم ساشي؟") == ["ساش"]

    def test_stop_words_are_normalized(self):
        # La liste est normalisée comme les textes : « où » et « être » y figurent sans accent.
        assert {"ou", "etre", "aafak"} <= STOP_WORDS

    def test_options(self):
        text = "Les sachets"
        assert tokenize(text, stem=False, remove_stop_words=False) == ["les", "sachets"]
        assert tokenize(text, stem=True, remove_stop_words=False) == ["les", "sachet"]
        assert tokenize(text, stem=False) == ["sachets"]

    def test_hyphen_apostrophe_and_underscore_separate_words(self):
        assert tokenize("kat-tb3o l'impression sac_kraft") == ["kat", "tbao", "impression", "sac", "kraft"]

    def test_repeated_letters_are_reduced(self):
        assert tokenize("salaaaam merciiii sachettt") == tokenize("salam merci sachet")

    def test_codes_are_kept_whole(self):
        assert tokenize("Code KB100L28, 80 g/m²") == ["code", "kb100l28", "80", "m2"]

    def test_mixed_scripts(self):
        assert tokenize("bghit 5000 ساشي kraft") == ["bghit", "5000", "ساش", "kraft"]

    def test_only_punctuation_or_emoji(self):
        assert tokenize("?!… 🙂🙂 ---") == []


class TestLightStem:
    @pytest.mark.parametrize("word, expected", [
        ("poignees", "poign"), ("poignee", "poign"), ("torsadees", "torsad"), ("torsade", "torsad"),
        ("sachets", "sachet"), ("commander", "command"), ("commande", "command"), ("proposez", "propos"),
        ("bags", "bag"), ("sac", "sac"), ("kraft", "kraft"), ("5000", "5000"),
    ])
    def test_latin(self, word, expected):
        assert light_stem(word) == expected

    @pytest.mark.parametrize("word, expected", [
        ("الطلبيه", "طلب"), ("طلبيه", "طلب"), ("الساكات", "ساك"), ("بالورق", "ورق"), ("والورق", "ورق"),
        ("ورق", "ورق"), ("للشركه", "شرك"), ("مسطحه", "مسطح"),
    ])
    def test_arabic(self, word, expected):
        assert light_stem(word) == expected

    def test_short_arabic_words_keep_a_root(self):
        assert light_stem("ال") == "ال"
        assert len(light_stem("الي")) >= 2


# ---------------------------------------------------------------- calcul local


class TestLocalHashEmbedder:
    def test_dimension_name_and_norm(self):
        embedder = LocalHashEmbedder()
        assert embedder.dim == DEFAULT_DIM == 512
        assert embedder.name == "local-hash-v1-512"
        vector = embedder.vector("Sachets fond plat en kraft")
        assert vector.dtype == np.float32
        assert vector.shape == (512,)
        assert np.linalg.norm(vector) == pytest.approx(1.0, abs=1e-5)

    def test_name_depends_on_dimension(self):
        assert LocalHashEmbedder(64).name != LocalHashEmbedder(128).name
        assert repr(LocalHashEmbedder(64)) == "LocalHashEmbedder(dim=64)"

    def test_invalid_dimension(self):
        with pytest.raises(ValueError):
            LocalHashEmbedder(8)

    def test_text_without_words_gives_zero_vector(self):
        embedder = LocalHashEmbedder()
        for text in ("", "   ", "?!", "le la les", None):
            assert not np.any(embedder.vector(text))

    def test_embed_returns_matrix_and_space(self):
        embedder = LocalHashEmbedder(64)
        result = embedder.embed(["sachet kraft", "", "ساشي"])
        assert isinstance(result, EmbeddingResult)
        assert result.vectors.shape == (3, 64)
        assert len(result) == 3
        assert result.embedder == embedder.name
        assert not np.any(result.vectors[1])
        assert embedder.embed([]).vectors.shape == (0, 64)

    def test_deterministic_within_process(self):
        assert np.array_equal(LocalHashEmbedder().vector("kif dima"), LocalHashEmbedder().vector("kif dima"))

    def test_deterministic_across_processes_with_random_hash_seed(self):
        """Le hachage BLAKE2b ne dépend pas de PYTHONHASHSEED, contrairement à hash()."""
        code = ("import sys; sys.path.insert(0, sys.argv[1]);"
                "from tecpap.rag.embeddings import LocalHashEmbedder;"
                "v = LocalHashEmbedder(64).vector('bghit 5000 ساشي kif dima');"
                "print(','.join(f'{x:.6f}' for x in v))")
        outputs = set()
        for seed in ("1", "12345"):
            env = {**os.environ, "PYTHONHASHSEED": seed}
            result = subprocess.run([sys.executable, "-c", code, str(REPO_ROOT)], capture_output=True, text=True,
                                    env=env, check=True, timeout=60)
            outputs.add(result.stdout.strip())
        assert len(outputs) == 1
        local = ",".join(f"{x:.6f}" for x in LocalHashEmbedder(64).vector("bghit 5000 ساشي kif dima"))
        assert outputs == {local}

    def test_related_texts_are_closer_than_unrelated(self):
        embedder = LocalHashEmbedder()
        question = embedder.vector("poignées torsadées")
        related = embedder.vector("Sac fond carré avec poignées torsadées, sac premium")
        unrelated = embedder.vector("Adresse : Parc Industriel CFCIM Bouskoura, Casablanca")
        assert cosine(question, related) > cosine(question, unrelated) + 0.2

    def test_spelling_variants_are_close(self):
        embedder = LocalHashEmbedder()
        assert cosine(embedder.vector("أكياس ورقية"), embedder.vector("اكياس ورقيه")) == pytest.approx(1.0, abs=1e-6)
        assert cosine(embedder.vector("kraft écru"), embedder.vector("KRAFT ECRU")) == pytest.approx(1.0, abs=1e-6)
        assert cosine(embedder.vector("b7al dima"), embedder.vector("bhal dima")) == pytest.approx(1.0, abs=1e-6)
        # Une faute de frappe garde une grande partie des n-grammes.
        assert cosine(embedder.vector("sachets torsadés"), embedder.vector("sachet torsadé")) > 0.6

    def test_features_are_word_and_character_ngrams(self):
        features = text_features("kb")
        assert set(features) == {" kb ", "# kb", "#kb ", "# kb "}
        assert text_features("sac sac")[" sac "] == 2

    def test_very_long_text_is_clipped(self):
        embedder = LocalHashEmbedder(32)
        long_text = "sachet " * 5000
        assert np.allclose(embedder.vector(long_text), embedder.vector(long_text + " poignée"))

    def test_embed_in(self):
        embedder = LocalHashEmbedder(32)
        assert embedder.embed_in(embedder.name, ["a b c"]).shape == (1, 32)
        assert embedder.embed_in("openai:text-embedding-3-small", ["texte"]) is None

    def test_implements_protocol(self):
        assert isinstance(LocalHashEmbedder(), Embedder)


# ---------------------------------------------------------------- modèle OpenAI


class TestOpenAIEmbedder:
    def test_uses_llm_client_embed(self):
        client = FakeLLMClient(embedding_dim=16)
        embedder = OpenAIEmbedder(client, "text-embedding-3-small")
        result = embedder.embed(["sachet", "kraft"])
        assert result.embedder == "openai:text-embedding-3-small" == embedder.name
        assert result.vectors.shape == (2, 16)
        assert np.allclose(np.linalg.norm(result.vectors, axis=1), 1.0, atol=1e-5)
        assert client.calls_to("embed") == [{"texts": ["sachet", "kraft"], "model": "text-embedding-3-small"}]

    def test_without_api_key_uses_local_space(self):
        client = unavailable_llm()
        embedder = OpenAIEmbedder(client, "text-embedding-3-small", fallback=LocalHashEmbedder(32))
        assert embedder.available is False
        assert embedder.name == "local-hash-v1-32"
        result = embedder.embed(["sachet"])
        assert result.embedder == "local-hash-v1-32"
        assert result.vectors.shape == (1, 32)
        assert client.calls_to("embed") == []

    @pytest.mark.parametrize("response", [
        None,
        [[0.1, 0.2]],  # un vecteur pour deux textes
        [[0.1, float("nan")], [0.3, 0.4]],
        [["a", "b"], ["c", "d"]],
        [[0.1, 0.2], [0.3]],
        [0.1, 0.2],
    ])
    def test_api_failure_or_invalid_answer_falls_back(self, response, caplog):
        client = FakeLLMClient()
        client.embed = lambda texts, *, model: response
        embedder = OpenAIEmbedder(client, "text-embedding-3-small", fallback=LocalHashEmbedder(32))
        with caplog.at_level(logging.WARNING, logger="tecpap.rag.embeddings"):
            result = embedder.embed(["sachet", "kraft"])
        assert result.embedder == "local-hash-v1-32"
        assert result.vectors.shape == (2, 32)
        assert "calcul local utilisé" in caplog.text
        # Le nom visé reste celui du modèle : une indexation suivante réessaiera l'API.
        assert embedder.name == "openai:text-embedding-3-small"

    def test_empty_texts_are_sent_as_a_space(self):
        client = FakeLLMClient(embedding_dim=8)
        OpenAIEmbedder(client, "m").embed(["", "x"])
        assert client.calls_to("embed")[0]["texts"] == [" ", "x"]

    def test_empty_list_makes_no_call(self):
        client = FakeLLMClient()
        result = OpenAIEmbedder(client, "m").embed([])
        assert len(result) == 0
        assert client.calls_to("embed") == []

    def test_embed_in_each_space(self):
        client = FakeLLMClient(embedding_dim=8)
        embedder = OpenAIEmbedder(client, "m", fallback=LocalHashEmbedder(32))
        assert embedder.embed_in("openai:m", ["x"]).shape == (1, 8)
        assert embedder.embed_in("local-hash-v1-32", ["x"]).shape == (1, 32)
        assert embedder.embed_in("openai:autre-modele", ["x"]) is None
        assert OpenAIEmbedder(unavailable_llm(), "m").embed_in("openai:m", ["x"]) is None

    def test_model_is_required(self):
        with pytest.raises(ValueError):
            OpenAIEmbedder(FakeLLMClient(), "")

    def test_from_settings(self, tmp_path):
        settings = Settings.for_tests(tmp_path, model_embedding="text-embedding-3-large")
        embedder = OpenAIEmbedder.from_settings(settings, FakeLLMClient())
        assert embedder.remote_name == "openai:text-embedding-3-large"
        assert "text-embedding-3-large" in repr(embedder)


# ---------------------------------------------------------------- sérialisation


class TestSerialization:
    def test_round_trip_float32_little_endian(self):
        vector = np.array([0.5, -1.25, 3.0], dtype=np.float64)
        blob = vector_to_blob(vector)
        assert blob == np.array([0.5, -1.25, 3.0], dtype="<f4").tobytes()
        assert len(blob) == 12
        restored = blob_to_vector(blob)
        assert restored.dtype == np.float32
        assert np.array_equal(restored, vector.astype(np.float32))

    def test_invalid_blob_size(self):
        with pytest.raises(ValueError):
            blob_to_vector(b"\x00\x01\x02")

    def test_normalize_rows(self):
        matrix = normalize_rows(np.array([[3.0, 4.0], [0.0, 0.0]]))
        assert matrix.dtype == np.float32
        assert np.allclose(matrix, [[0.6, 0.8], [0.0, 0.0]])
        assert normalize_rows(np.array([2.0, 0.0])).shape == (1, 2)
