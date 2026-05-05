from __future__ import annotations

import pytest

from domain.entities.email_message import EmailMessage
from infrastructure.parsers.openai_email_parser import (
    OpenAIEmailParser,
    _supports_json_response_format,
    _supports_temperature_control,
    _uses_responses_api,
    _uses_max_completion_tokens,
)


def test_uses_max_completion_tokens_for_new_models():
    assert _uses_max_completion_tokens("gpt-5-mini") is True
    assert _uses_max_completion_tokens("o1-preview") is True
    assert _uses_max_completion_tokens("o3-mini") is True
    assert _uses_max_completion_tokens("o4") is True


def test_uses_max_completion_tokens_for_classical_models():
    assert _uses_max_completion_tokens("gpt-4o-mini") is False
    assert _uses_max_completion_tokens("gpt-4-turbo") is False
    assert _uses_max_completion_tokens("gpt-3.5-turbo") is False


def test_supports_json_response_format():
    assert _supports_json_response_format("gpt-4o-mini") is True
    assert _supports_json_response_format("gpt-5-mini") is True
    assert _supports_json_response_format("text-davinci-003") is False
    assert _supports_json_response_format("") is False


def test_uses_responses_api_for_new_models():
    assert _uses_responses_api("gpt-5-mini") is True
    assert _uses_responses_api("o1-preview") is True
    assert _uses_responses_api("o3-mini") is True
    assert _uses_responses_api("gpt-4o-mini") is False


def test_supports_temperature_control():
    assert _supports_temperature_control("gpt-4o-mini") is True
    assert _supports_temperature_control("gpt-3.5-turbo") is True
    assert _supports_temperature_control("gpt-5-mini") is False
    assert _supports_temperature_control("o1-preview") is False


@pytest.mark.asyncio
async def test_parse_returns_degraded_response_when_client_missing():
    parser = OpenAIEmailParser.__new__(OpenAIEmailParser)
    parser._client = None
    email = EmailMessage(sender="a@b.com", subject=None, body_text="hi")
    extracted, response_id, reasons = await parser.parse(email)
    assert extracted == {}
    assert response_id is None
    assert reasons == ["OpenAI API not configured"]


@pytest.mark.asyncio
async def test_parse_returns_extracted_when_origin_destination_present(monkeypatch):
    parser = OpenAIEmailParser.__new__(OpenAIEmailParser)
    parser._client = object()

    monkeypatch.setattr(
        parser,
        "_parse_with_openai",
        lambda email: ({"origin": "CDG", "destination": "JFK"}, "resp-1"),
    )

    email = EmailMessage(sender="a@b.com", subject=None, body_text="hi")
    extracted, _, reasons = await parser.parse(email)
    assert extracted == {"origin": "CDG", "destination": "JFK"}
    assert reasons is None


@pytest.mark.asyncio
async def test_parse_calls_failure_reasons_when_origin_missing(monkeypatch):
    parser = OpenAIEmailParser.__new__(OpenAIEmailParser)
    parser._client = object()

    monkeypatch.setattr(
        parser,
        "_parse_with_openai",
        lambda email: ({"destination": "JFK"}, "resp-1"),
    )
    monkeypatch.setattr(
        parser,
        "_failure_reasons",
        lambda email: (["Missing origin"], "resp-2"),
    )

    email = EmailMessage(sender="a@b.com", subject=None, body_text="hi")
    extracted, _, reasons = await parser.parse(email)
    assert extracted == {"destination": "JFK"}
    assert reasons == ["Missing origin"]


@pytest.mark.asyncio
async def test_parse_with_openai_uses_responses_api_for_gpt5(monkeypatch):
    from config import settings

    class DummyResponses:
        def __init__(self):
            self.last_kwargs = None

        def create(self, **kwargs):
            self.last_kwargs = kwargs

            class Resp:
                id = "resp-1"
                model = kwargs.get("model")
                status = "completed"
                output_text = '{"origin":"CDG","destination":"JFK"}'

            return Resp()

    class DummyClient:
        def __init__(self):
            self.responses = DummyResponses()
            self.chat = None  # should not be used

    parser = OpenAIEmailParser.__new__(OpenAIEmailParser)
    parser._client = DummyClient()

    monkeypatch.setattr(settings, "openai_model", "gpt-5-mini")
    email = EmailMessage(sender="a@b.com", subject="s", body_text="hi")

    extracted, response_id = parser._parse_with_openai(email)
    assert response_id == "resp-1"
    assert extracted["origin"] == "CDG"
    assert extracted["destination"] == "JFK"

    kwargs = parser._client.responses.last_kwargs
    assert kwargs["model"] == "gpt-5-mini"
    assert "max_output_tokens" in kwargs
    assert "temperature" not in kwargs


@pytest.mark.asyncio
async def test_parse_with_openai_uses_chat_api_and_temperature_for_gpt4o(monkeypatch):
    from config import settings

    class DummyChatCompletions:
        def __init__(self):
            self.last_kwargs = None

        def create(self, **kwargs):
            self.last_kwargs = kwargs

            class Msg:
                content = '{"origin":"CDG","destination":"JFK"}'

            class Choice:
                message = Msg()
                finish_reason = "stop"

            class Resp:
                id = "chat-1"
                model = kwargs.get("model")
                choices = [Choice()]

            return Resp()

    class DummyChat:
        def __init__(self):
            self.completions = DummyChatCompletions()

    class DummyClient:
        def __init__(self):
            self.chat = DummyChat()
            self.responses = None  # should not be used

    parser = OpenAIEmailParser.__new__(OpenAIEmailParser)
    parser._client = DummyClient()

    monkeypatch.setattr(settings, "openai_model", "gpt-4o-mini")
    email = EmailMessage(sender="a@b.com", subject="s", body_text="hi")

    extracted, response_id = parser._parse_with_openai(email)
    assert response_id == "chat-1"
    assert extracted["origin"] == "CDG"
    assert extracted["destination"] == "JFK"

    kwargs = parser._client.chat.completions.last_kwargs
    assert kwargs["model"] == "gpt-4o-mini"
    assert kwargs["temperature"] == 0
    assert "messages" in kwargs
