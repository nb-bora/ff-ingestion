from __future__ import annotations

import pytest

from application.use_cases.notify_failure_use_case import NotifyFailureUseCase
from config import settings
from domain.entities.email_message import EmailMessage
from domain.enums.failure_code import FailureCode
from domain.value_objects.error_info import ErrorInfo


class _RecPublisher:
    def __init__(self, *, raise_on_publish=False):
        self.events = []
        self.raise_on_publish = raise_on_publish

    async def publish(self, event):
        if self.raise_on_publish:
            raise RuntimeError("publish boom")
        self.events.append(event)


@pytest.mark.asyncio
async def test_user_untreatable_publishes_event(monkeypatch):
    monkeypatch.setattr(settings, "notifications_enabled", True, raising=False)
    pub = _RecPublisher()
    uc = NotifyFailureUseCase(publisher=pub)
    email = EmailMessage(sender="u@x.com", subject="hi", body_text="body")

    await uc.user_untreatable(email=email, code=FailureCode.PARSE_FAILED)

    assert len(pub.events) == 1
    ev = pub.events[0]
    assert ev.category.value == "user_untreatable"
    assert ev.recipient.email == "u@x.com"


@pytest.mark.asyncio
async def test_user_untreatable_uses_rule_codes_when_no_missing_fields(monkeypatch):
    monkeypatch.setattr(settings, "notifications_enabled", True, raising=False)
    pub = _RecPublisher()
    uc = NotifyFailureUseCase(publisher=pub)
    email = EmailMessage(sender="u@x.com", subject="hi", body_text="body")

    await uc.user_untreatable(
        email=email,
        code=FailureCode.T1_R3_CITY_DATE_REQUIRED,
        rule_codes=[FailureCode.T1_R3_CITY_DATE_REQUIRED],
    )

    assert len(pub.events) == 1
    payload = pub.events[0].to_dict()
    assert len(payload["variables"]["missing_fields"]) > 0
    assert payload["variables"]["blocking_rules"] == ["T1_R3_CITY_DATE_REQUIRED"]


@pytest.mark.asyncio
async def test_support_alert_throttle(monkeypatch):
    monkeypatch.setattr(settings, "notifications_enabled", True, raising=False)
    monkeypatch.setattr(
        settings, "support_alert_throttle_seconds", 9999, raising=False
    )
    pub = _RecPublisher()
    uc = NotifyFailureUseCase(publisher=pub)
    err = ErrorInfo(class_="X", message="boom")

    await uc.support_alert(code=FailureCode.POISON_MESSAGE, error=err)
    await uc.support_alert(code=FailureCode.POISON_MESSAGE, error=err)
    await uc.support_alert(code=FailureCode.OPENAI_UNAVAILABLE, error=err)

    assert len(pub.events) == 2
    assert {e.failure_code for e in pub.events} == {
        FailureCode.POISON_MESSAGE,
        FailureCode.OPENAI_UNAVAILABLE,
    }


@pytest.mark.asyncio
async def test_support_alert_throttle_disabled_when_zero(monkeypatch):
    monkeypatch.setattr(settings, "notifications_enabled", True, raising=False)
    monkeypatch.setattr(
        settings, "support_alert_throttle_seconds", 0, raising=False
    )
    pub = _RecPublisher()
    uc = NotifyFailureUseCase(publisher=pub)
    err = ErrorInfo(class_="X", message="boom")

    await uc.support_alert(code=FailureCode.POISON_MESSAGE, error=err)
    await uc.support_alert(code=FailureCode.POISON_MESSAGE, error=err)

    assert len(pub.events) == 2


@pytest.mark.asyncio
async def test_does_not_raise_when_publisher_fails(monkeypatch):
    monkeypatch.setattr(settings, "notifications_enabled", True, raising=False)
    pub = _RecPublisher(raise_on_publish=True)
    uc = NotifyFailureUseCase(publisher=pub)
    email = EmailMessage(sender="u@x.com", subject="hi", body_text="body")

    await uc.user_untreatable(email=email, code=FailureCode.PARSE_FAILED)

    err = ErrorInfo(class_="X", message="boom")
    await uc.support_alert(code=FailureCode.POISON_MESSAGE, error=err)


@pytest.mark.asyncio
async def test_skips_when_notifications_disabled(monkeypatch):
    monkeypatch.setattr(settings, "notifications_enabled", False, raising=False)
    pub = _RecPublisher()
    uc = NotifyFailureUseCase(publisher=pub)
    email = EmailMessage(sender="u@x.com", subject="hi", body_text="body")

    await uc.user_untreatable(email=email, code=FailureCode.PARSE_FAILED)
    err = ErrorInfo(class_="X", message="boom")
    await uc.support_alert(code=FailureCode.POISON_MESSAGE, error=err)

    assert pub.events == []
