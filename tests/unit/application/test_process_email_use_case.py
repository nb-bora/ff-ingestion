from __future__ import annotations

import pytest

from application.use_cases.process_email_use_case import ProcessEmailUseCase
from domain.entities.email_message import EmailMessage
from shared.exceptions import MissingSenderError


class _FakeParseUC:
    def __init__(self, *, fare_event):
        self.fare_event = fare_event
        self.calls = 0

    async def execute(self, _email):
        self.calls += 1
        return self.fare_event


class _FakePublisher:
    def __init__(self):
        self.published: list[dict] = []

    async def publish_fare_event(self, fare_event):
        self.published.append(fare_event)


@pytest.mark.asyncio
async def test_process_email_publishes_fare_event():
    fare = {
        "id": "fe-1",
        "sender": "a@b.com",
        "parsed_at": "2024-01-01T00:00:00Z",
        "email_body_length": 10,
        "status": "parsed",
    }
    publisher = _FakePublisher()
    uc = ProcessEmailUseCase(
        parse_email=_FakeParseUC(fare_event=fare),
        publisher=publisher,
    )
    email = EmailMessage(sender="a@b.com", subject="s", body_text="hi")

    result = await uc.execute(email)

    assert result == fare
    assert publisher.published == [fare]


@pytest.mark.asyncio
async def test_process_email_missing_sender_raises_and_no_publish():
    publisher = _FakePublisher()
    uc = ProcessEmailUseCase(
        parse_email=_FakeParseUC(fare_event={}),
        publisher=publisher,
    )
    email = EmailMessage(sender="", subject=None, body_text="hi")

    with pytest.raises(MissingSenderError):
        await uc.execute(email)
    assert publisher.published == []
