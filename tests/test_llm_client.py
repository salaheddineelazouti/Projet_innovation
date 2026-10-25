"""Tests du client des modèles de langue (tecpap.llm.client) avec un SDK OpenAI simulé.

La doublure ``FakeLLMClient`` (tests/fakes.py) et un client de ce module (classification de
priorité des emails) sont aussi vérifiés ici.
"""

import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import openai
import pytest

from tecpap.config import Settings
from tecpap.extraction.prompts import PRIORITY_SYSTEM_PROMPT
from tecpap.llm import client as client_module
from tecpap.llm.client import ChatResult, LLMClient, ToolCall, parse_json_object, strip_code_fence
from tecpap.reporting.analytics import AIPredictor
from tests.fakes import FakeLLMClient

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "tecpap"


def _sdk_error(error_class):
    """Instance d'une erreur du SDK, construite sans requête HTTP réelle."""
    return error_class.__new__(error_class)


def _completion(content=None, tool_calls=None, model="gpt-test"):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], model=model)


def _tool_call(call_id, name, arguments):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


@pytest.fixture
def sdk():
    return MagicMock()


@pytest.fixture
def llm(sdk):
    return LLMClient(api_key=None, client=sdk, retry_delay=0)


class TestAvailability:
    """Disponibilité du client selon la clé d'API."""

    def test_unavailable_without_key(self):
        assert LLMClient(api_key=None).available is False

    def test_empty_key_is_no_key(self):
        assert LLMClient(api_key="").available is False

    def test_available_with_key(self):
        assert LLMClient(api_key="sk-test").available is True

    def test_available_with_injected_client(self, llm):
        assert llm.available is True

    def test_from_settings_without_key(self, tmp_path):
        assert LLMClient.from_settings(Settings.for_tests(tmp_path)).available is False

    def test_from_settings_reads_key_and_timeout(self, tmp_path):
        settings = Settings.for_tests(tmp_path, openai_api_key="sk-test", llm_timeout=12.5)
        llm = LLMClient.from_settings(settings)
        assert llm.available is True
        assert llm.timeout == 12.5

    def test_repr_hides_the_key(self):
        assert "sk-secret" not in repr(LLMClient(api_key="sk-secret"))

    def test_calls_without_key_return_none(self, tmp_path):
        llm = LLMClient(api_key=None)
        audio = tmp_path / "note.ogg"
        audio.write_bytes(b"0" * 200)
        assert llm.chat_json(model="m", system="s", user="JSON") is None
        assert llm.chat(model="m", messages=[{"role": "user", "content": "x"}]) is None
        assert llm.transcribe(str(audio), model="whisper-1") is None
        assert llm.image_to_text(b"\x89PNG", "image/png", model="m", instruction="lis") is None
        assert llm.embed(["texte"], model="e") is None

    def test_sdk_is_built_lazily_without_its_own_retries(self):
        with patch.object(client_module.openai, "OpenAI") as openai_class:
            llm = LLMClient(api_key="sk-test", timeout=7.0)
            openai_class.assert_not_called()
            openai_class.return_value.chat.completions.create.return_value = _completion('{"a": 1}')
            assert llm.chat_json(model="m", system="s", user="JSON") == {"a": 1}
            llm.chat_json(model="m", system="s", user="JSON")
        openai_class.assert_called_once_with(api_key="sk-test", timeout=7.0, max_retries=0)


class TestChatJson:
    """Réponses au format objet JSON."""

    def test_request_parameters(self, llm, sdk):
        sdk.chat.completions.create.return_value = _completion('{"quantite": 500}')
        result = llm.chat_json(model="gpt-test", system="systeme", user="donne du JSON", temperature=0.2,
                               max_tokens=50)
        assert result == {"quantite": 500}
        kwargs = sdk.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "gpt-test"
        assert kwargs["response_format"] == {"type": "json_object"}
        assert kwargs["temperature"] == 0.2
        assert kwargs["max_tokens"] == 50
        assert kwargs["messages"] == [
            {"role": "system", "content": "systeme"},
            {"role": "user", "content": "donne du JSON"},
        ]

    def test_code_fence_is_stripped(self, llm, sdk):
        sdk.chat.completions.create.return_value = _completion('```json\n{"est_bon_commande": true}\n```')
        assert llm.chat_json(model="m", system="s", user="JSON") == {"est_bon_commande": True}

    def test_text_around_the_object_is_ignored(self, llm, sdk):
        sdk.chat.completions.create.return_value = _completion('Voici le résultat : {"quantite": 3} merci')
        assert llm.chat_json(model="m", system="s", user="JSON") == {"quantite": 3}

    @pytest.mark.parametrize("content", ["pas du JSON", "[1, 2]", "", None, "```json\n{tronqué"])
    def test_unreadable_answer_returns_none(self, llm, sdk, content):
        sdk.chat.completions.create.return_value = _completion(content)
        assert llm.chat_json(model="m", system="s", user="JSON") is None

    def test_unexpected_response_shape_returns_none(self, llm, sdk):
        sdk.chat.completions.create.return_value = SimpleNamespace(choices=[])
        assert llm.chat_json(model="m", system="s", user="JSON") is None


class TestRetry:
    """Un seul nouvel essai, sur erreur passagère uniquement."""

    @pytest.mark.parametrize("error_class", [
        openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError,
    ])
    def test_transient_error_is_retried_once(self, llm, sdk, error_class):
        sdk.chat.completions.create.side_effect = [_sdk_error(error_class), _completion('{"ok": true}')]
        assert llm.chat_json(model="m", system="s", user="JSON") == {"ok": True}
        assert sdk.chat.completions.create.call_count == 2

    def test_two_transient_errors_give_none(self, llm, sdk):
        sdk.chat.completions.create.side_effect = [_sdk_error(openai.RateLimitError)] * 3
        assert llm.chat_json(model="m", system="s", user="JSON") is None
        assert sdk.chat.completions.create.call_count == 2

    @pytest.mark.parametrize("error", [
        _sdk_error(openai.BadRequestError), _sdk_error(openai.AuthenticationError), RuntimeError("panne"),
    ])
    def test_other_errors_are_not_retried(self, llm, sdk, error):
        sdk.chat.completions.create.side_effect = error
        assert llm.chat_json(model="m", system="s", user="JSON") is None
        assert sdk.chat.completions.create.call_count == 1

    def test_backoff_pause_before_the_retry(self, sdk):
        llm = LLMClient(api_key=None, client=sdk, retry_delay=0.5)
        sdk.chat.completions.create.side_effect = [_sdk_error(openai.APITimeoutError), _completion("{}")]
        with patch.object(client_module.time, "sleep") as sleep:
            assert llm.chat_json(model="m", system="s", user="JSON") == {}
        sleep.assert_called_once_with(0.5)


class TestChat:
    """Conversation et appels d'outils."""

    def test_plain_answer(self, llm, sdk):
        sdk.chat.completions.create.return_value = _completion("Bonjour", model="gpt-4o-2024")
        result = llm.chat(model="gpt-4o", messages=[{"role": "user", "content": "salam"}])
        assert result == ChatResult(content="Bonjour", tool_calls=[], model="gpt-4o-2024")
        kwargs = sdk.chat.completions.create.call_args.kwargs
        assert "tools" not in kwargs
        assert "tool_choice" not in kwargs
        assert kwargs["max_tokens"] == 800

    def test_tools_and_tool_choice_are_sent(self, llm, sdk):
        tools = [{"type": "function", "function": {"name": "my_recent_orders", "parameters": {"type": "object"}}}]
        sdk.chat.completions.create.return_value = _completion(None, [_tool_call("c1", "my_recent_orders", "{}")])
        llm.chat(model="m", messages=[], tools=tools, tool_choice="auto")
        kwargs = sdk.chat.completions.create.call_args.kwargs
        assert kwargs["tools"] == tools
        assert kwargs["tool_choice"] == "auto"

    def test_tool_choice_without_tools_is_not_sent(self, llm, sdk):
        sdk.chat.completions.create.return_value = _completion("x")
        llm.chat(model="m", messages=[], tool_choice="auto")
        assert "tool_choice" not in sdk.chat.completions.create.call_args.kwargs

    def test_tool_call_arguments_are_decoded(self, llm, sdk):
        sdk.chat.completions.create.return_value = _completion(
            None, [_tool_call("c1", "my_order_status", '{"reference": "CMD-12"}')]
        )
        result = llm.chat(model="m", messages=[], tools=[{"type": "function"}])
        assert result.content is None
        assert result.tool_calls == [ToolCall(id="c1", name="my_order_status", arguments={"reference": "CMD-12"})]

    @pytest.mark.parametrize("arguments", ["{pas du json", "[1]", "", None])
    def test_unreadable_tool_arguments_give_empty_dict(self, llm, sdk, arguments):
        sdk.chat.completions.create.return_value = _completion(None, [_tool_call("c1", "outil", arguments)])
        result = llm.chat(model="m", messages=[], tools=[{"type": "function"}])
        assert result.tool_calls[0].arguments == {}

    def test_error_returns_none(self, llm, sdk):
        sdk.chat.completions.create.side_effect = RuntimeError("panne")
        assert llm.chat(model="m", messages=[]) is None


class TestTranscribe:
    """Transcription audio."""

    @pytest.fixture
    def audio(self, tmp_path):
        path = tmp_path / "note.ogg"
        path.write_bytes(b"0" * 200)
        return str(path)

    def test_automatic_language_by_default(self, llm, sdk, audio):
        sdk.audio.transcriptions.create.return_value = " bghit 3000 sachet \n"
        assert llm.transcribe(audio, model="whisper-1") == "bghit 3000 sachet"
        kwargs = sdk.audio.transcriptions.create.call_args.kwargs
        assert kwargs["model"] == "whisper-1"
        assert kwargs["response_format"] == "text"
        assert "language" not in kwargs
        assert "prompt" not in kwargs
        assert kwargs["file"].name == audio

    def test_language_and_prompt_are_sent(self, llm, sdk, audio):
        sdk.audio.transcriptions.create.return_value = "texte"
        llm.transcribe(audio, model="whisper-1", language="ar", prompt="sachets kraft")
        kwargs = sdk.audio.transcriptions.create.call_args.kwargs
        assert kwargs["language"] == "ar"
        assert kwargs["prompt"] == "sachets kraft"

    def test_object_with_text_attribute(self, llm, sdk, audio):
        sdk.audio.transcriptions.create.return_value = SimpleNamespace(text="jouj alaf sachet")
        assert llm.transcribe(audio, model="whisper-1") == "jouj alaf sachet"

    def test_empty_transcription_returns_none(self, llm, sdk, audio):
        sdk.audio.transcriptions.create.return_value = "   "
        assert llm.transcribe(audio, model="whisper-1") is None

    def test_missing_file_returns_none(self, llm, sdk, tmp_path):
        assert llm.transcribe(str(tmp_path / "absent.ogg"), model="whisper-1") is None
        sdk.audio.transcriptions.create.assert_not_called()


class TestImageToText:
    """Lecture d'image par le modèle de vision."""

    def test_image_is_sent_as_data_url(self, llm, sdk):
        sdk.chat.completions.create.return_value = _completion(" 2000 sachets ")
        text = llm.image_to_text(b"\x89PNG-octets", "image/png", model="vision-test", instruction="Lis le texte")
        assert text == "2000 sachets"
        kwargs = sdk.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "vision-test"
        content = kwargs["messages"][0]["content"]
        assert content[0] == {"type": "text", "text": "Lis le texte"}
        expected_url = "data:image/png;base64," + base64.b64encode(b"\x89PNG-octets").decode("ascii")
        assert content[1]["image_url"]["url"] == expected_url

    def test_empty_answer_returns_none(self, llm, sdk):
        sdk.chat.completions.create.return_value = _completion("")
        assert llm.image_to_text(b"x", "image/jpeg", model="m", instruction="lis") is None


class TestEmbed:
    """Plongements vectoriels."""

    @staticmethod
    def _embeddings(*vectors, reverse=False):
        items = [SimpleNamespace(index=i, embedding=v) for i, v in enumerate(vectors)]
        return SimpleNamespace(data=list(reversed(items)) if reverse else items)

    def test_vectors_follow_input_order(self, llm, sdk):
        sdk.embeddings.create.return_value = self._embeddings([1.0, 0.0], [0.0, 1.0], reverse=True)
        assert llm.embed(["a", "b"], model="emb") == [[1.0, 0.0], [0.0, 1.0]]
        assert sdk.embeddings.create.call_args.kwargs == {"model": "emb", "input": ["a", "b"]}

    def test_empty_input_makes_no_call(self, llm, sdk):
        assert llm.embed([], model="emb") == []
        sdk.embeddings.create.assert_not_called()

    def test_texts_are_sent_in_batches(self, llm, sdk, monkeypatch):
        monkeypatch.setattr(client_module, "EMBEDDING_BATCH_SIZE", 2)
        sdk.embeddings.create.side_effect = [self._embeddings([1.0], [2.0]), self._embeddings([3.0])]
        assert llm.embed(["a", "b", "c"], model="emb") == [[1.0], [2.0], [3.0]]
        assert sdk.embeddings.create.call_count == 2

    def test_incomplete_answer_returns_none(self, llm, sdk):
        sdk.embeddings.create.return_value = self._embeddings([1.0])
        assert llm.embed(["a", "b"], model="emb") is None


class TestJsonParsing:
    """Décodage tolérant des réponses JSON."""

    @pytest.mark.parametrize("text, expected", [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('```\n{"a": 1}\n```', {"a": 1}),
        ('```json{"a": 1}```', {"a": 1}),
        ('Réponse : {"a": {"b": 2}} fin', {"a": {"b": 2}}),
        ("[1, 2]", None),
        ("", None),
        (None, None),
    ])
    def test_parse_json_object(self, text, expected):
        assert parse_json_object(text) == expected

    def test_strip_code_fence_keeps_plain_text(self):
        assert strip_code_fence('  {"a": 1}  ') == '{"a": 1}'


class TestFakeLLMClient:
    """Doublure du client utilisée par les autres tests."""

    def test_scripted_answers_in_order_then_none(self):
        fake = FakeLLMClient(chat_json=[{"a": 1}, {"b": 2}])
        assert fake.chat_json(model="m", system="s", user="u") == {"a": 1}
        assert fake.chat_json(model="m", system="s", user="u") == {"b": 2}
        assert fake.chat_json(model="m", system="s", user="u") is None
        assert len(fake.calls_to("chat_json")) == 3

    def test_callable_answer_receives_the_arguments(self):
        fake = FakeLLMClient(transcribe=[lambda **kwargs: kwargs["model"]])
        assert fake.transcribe("note.ogg", model="whisper-1") == "whisper-1"

    def test_chat_string_becomes_chat_result(self):
        fake = FakeLLMClient().queue("chat", "Bonjour")
        assert fake.chat(model="m", messages=[]) == ChatResult(content="Bonjour", tool_calls=[], model="m")

    def test_unavailable_fake_records_and_returns_none(self):
        fake = FakeLLMClient(chat_json=[{"a": 1}], available=False)
        assert fake.chat_json(model="m", system="s", user="u") is None
        assert fake.embed(["x"], model="e") is None
        assert [name for name, _ in fake.calls] == ["chat_json", "embed"]

    def test_embed_is_deterministic_and_normalized(self):
        fake = FakeLLMClient(embedding_dim=32)
        first, again, other = fake.embed(["sachets kraft", "sachets kraft", "poignées torsadées"], model="e")
        assert first == again
        assert len(first) == 32
        assert sum(value * value for value in first) == pytest.approx(1.0)
        assert first != other

    def test_embed_similar_texts_are_closer(self):
        fake = FakeLLMClient(embedding_dim=256)
        base, close, far = fake.embed(["sachets kraft fond plat", "sachets kraft", "livraison demain matin"],
                                      model="e")

        def cosine(a, b):
            return sum(x * y for x, y in zip(a, b, strict=True))

        assert cosine(base, close) > cosine(base, far)


class TestPriorityClassification:
    """Classification de priorité des emails (AIPredictor), qui passe par le client des modèles."""

    def _predictor(self, tmp_path, llm):
        return AIPredictor(MagicMock(), settings=Settings.for_tests(tmp_path, model_light="leger-test"), llm=llm)

    def test_default_without_api_key(self, tmp_path):
        llm = FakeLLMClient(available=False)
        assert self._predictor(tmp_path, llm).classify_email_priority("Sujet", "Corps") == AIPredictor.DEFAULT_PRIORITY
        assert llm.calls == []

    def test_answer_is_filtered_and_completed(self, tmp_path):
        llm = FakeLLMClient(chat_json=[{"priority": "high", "is_urgent": True, "inattendu": "x"}])
        result = self._predictor(tmp_path, llm).classify_email_priority("Urgent", "Livraison demain")
        assert result == {"priority": "high", "is_purchase_order": False, "is_urgent": True, "confidence": 50}
        call = llm.calls_to("chat_json")[0]
        assert call["model"] == "leger-test"
        assert call["system"] == PRIORITY_SYSTEM_PROMPT
        assert "Livraison demain" in call["user"]

    def test_unknown_priority_becomes_medium(self, tmp_path):
        llm = FakeLLMClient(chat_json=[{"priority": "critique"}])
        assert self._predictor(tmp_path, llm).classify_email_priority("a", "b")["priority"] == "medium"

    def test_failure_gives_default(self, tmp_path):
        llm = FakeLLMClient(chat_json=[None])
        assert self._predictor(tmp_path, llm).classify_email_priority("a", "b") == AIPredictor.DEFAULT_PRIORITY

    def test_predictor_builds_its_client_from_settings(self, tmp_path):
        predictor = AIPredictor(MagicMock(), settings=Settings.for_tests(tmp_path))
        assert isinstance(predictor.llm, LLMClient)
        assert predictor.llm.available is False


class TestSdkIsolation:
    """Le SDK OpenAI n'est importé que par tecpap.llm.client."""

    def test_openai_is_imported_only_by_the_client(self):
        importers = []
        for path in PACKAGE_DIR.rglob("*.py"):
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith(("import openai", "from openai")):
                    importers.append(path.relative_to(PACKAGE_DIR).as_posix())
        assert importers == ["llm/client.py"]
