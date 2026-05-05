from __future__ import annotations

import pytest

from domain.entities.email_message import EmailMessage
from infrastructure.parsers.openai_email_parser import (
    OpenAIEmailParser,
    _supports_json_response_format,
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
    extracted, response_id, reasons = await parser.parse(email)
    assert extracted == {"origin": "CDG", "destination": "JFK"}
    assert response_id == "resp-1"
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
    extracted, response_id, reasons = await parser.parse(email)
    assert extracted == {"destination": "JFK"}
    assert reasons == ["Missing origin"]
