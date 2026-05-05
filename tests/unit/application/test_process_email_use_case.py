from __future__ import annotations

import asyncio

import pytest

from application.use_cases.process_email_use_case import ProcessEmailUseCase
from domain.entities.email_message import EmailMessage
from domain.enums.failure_code import FailureCode
from shared.exceptions import MissingSenderError, ParseError


class _FakeParseUC:
    def __init__(self, *, fare_event=None, raise_exc=None):
        self.fare_event = fare_event
        self.raise_exc = raise_exc
        self.calls = 0

    async def execute(self, _email):
        await asyncio.sleep(0)
        self.calls += 1
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.fare_event


class _FakePublisher:
    def __init__(self):
        self.published: list[dict] = []

    async def publish_fare_event(self, fare_event):
        await asyncio.sleep(0)
        self.published.append(fare_event)


class _FakeNotifyUC:
    def __init__(self):
        self.user_calls: list[dict] = []
        self.support_calls: list[dict] = []

    async def user_untreatable(self, **kwargs):
        await asyncio.sleep(0)
        self.user_calls.append(kwargs)

    async def support_alert(self, **kwargs):
        await asyncio.sleep(0)
        self.support_calls.append(kwargs)


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
    notify = _FakeNotifyUC()
    uc = ProcessEmailUseCase(
        parse_email=_FakeParseUC(fare_event=fare),
        publisher=publisher,
        notify_failure=notify,
    )
    email = EmailMessage(sender="a@b.com", subject="s", body_text="hi")

    result = await uc.execute(email)

    assert result == fare
    assert publisher.published == [fare]
    assert notify.user_calls == []
    assert notify.support_calls == []


@pytest.mark.asyncio
async def test_process_email_parsing_failed_emits_user_untreatable_and_no_publish():
    fare = {
        "id": "fe-1",
        "sender": "a@b.com",
        "parsed_at": "2024-01-01T00:00:00Z",
        "email_body_length": 10,
        "status": "parsing_failed",
        "failure_reasons": ["Missing origin"],
    }
    publisher = _FakePublisher()
    notify = _FakeNotifyUC()
    uc = ProcessEmailUseCase(
        parse_email=_FakeParseUC(fare_event=fare),
        publisher=publisher,
        notify_failure=notify,
    )
    email = EmailMessage(sender="a@b.com", subject="s", body_text="hi")

    result = await uc.execute(email)

    assert result == fare
    assert publisher.published == []
    assert len(notify.user_calls) == 1
    call = notify.user_calls[0]
    assert call["email"] is email
    assert call["code"] == FailureCode.PARSE_FAILED


@pytest.mark.asyncio
async def test_process_email_missing_sender_raises_and_no_publish():
    publisher = _FakePublisher()
    notify = _FakeNotifyUC()
    uc = ProcessEmailUseCase(
        parse_email=_FakeParseUC(fare_event={}),
        publisher=publisher,
        notify_failure=notify,
    )
    email = EmailMessage(sender="", subject=None, body_text="hi")

    with pytest.raises(MissingSenderError):
        await uc.execute(email)
    assert publisher.published == []
    assert notify.user_calls == []


@pytest.mark.asyncio
async def test_process_email_parse_error_emits_user_untreatable_and_reraises():
    publisher = _FakePublisher()
    notify = _FakeNotifyUC()
    uc = ProcessEmailUseCase(
        parse_email=_FakeParseUC(raise_exc=ParseError("OpenAI failed")),
        publisher=publisher,
        notify_failure=notify,
    )
    email = EmailMessage(sender="a@b.com", subject="s", body_text="hi")

    with pytest.raises(ParseError):
        await uc.execute(email)

    assert publisher.published == []
    assert len(notify.user_calls) == 1
    call = notify.user_calls[0]
    assert call["email"] is email
    assert call["code"] == FailureCode.PARSE_FAILED
