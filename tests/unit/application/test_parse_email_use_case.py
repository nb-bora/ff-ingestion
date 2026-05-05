from __future__ import annotations

import pytest

from application.use_cases.parse_email_use_case import ParseEmailUseCase
from domain.entities.email_message import EmailMessage
from domain.value_objects.email_metadata import EmailThreadMetadata
from shared.exceptions import MissingSenderError


class _FakeParser:
    def __init__(self, *, extracted, response_id="resp-1", reasons=None):
        self.extracted = extracted
        self.response_id = response_id
        self.reasons = reasons

    async def parse(self, _email):
        return self.extracted, self.response_id, self.reasons


@pytest.mark.asyncio
async def test_parse_email_missing_sender_raises():
    uc = ParseEmailUseCase(parser=_FakeParser(extracted={}))
    email = EmailMessage(sender="", subject=None, body_text="hi")
    with pytest.raises(MissingSenderError):
        await uc.execute(email)


@pytest.mark.asyncio
async def test_parse_email_valid_extraction_returns_parsed_status():
    parser = _FakeParser(extracted={"origin": "CDG", "destination": "JFK"})
    uc = ParseEmailUseCase(parser=parser)
    email = EmailMessage(
        sender="a@b.com",
        subject="trip",
        body_text="Paris to NYC",
        thread=EmailThreadMetadata(message_id="<m-1@x>"),
    )
    fare = await uc.execute(email)
    assert fare["status"] == "parsed"
    assert fare["sender"] == "a@b.com"
    assert fare["extracted_travel"] == {"origin": "CDG", "destination": "JFK"}
    assert fare["openai_response_id"] == "resp-1"


@pytest.mark.asyncio
async def test_parse_email_invalid_extraction_returns_failed_status():
    parser = _FakeParser(extracted={}, reasons=["Missing origin"])
    uc = ParseEmailUseCase(parser=parser)
    email = EmailMessage(sender="a@b.com", subject=None, body_text="?")
    fare = await uc.execute(email)
    assert fare["status"] == "parsing_failed"
    assert fare["failure_reasons"] == ["Missing origin"]
