from __future__ import annotations

import json

import pytest

from domain.entities.notification_event import NotificationEvent
from domain.enums.failure_code import FailureCode
from domain.value_objects.error_info import ErrorInfo
from infrastructure.messaging.sqs_notification_publisher import (
    SQSNotificationPublisher,
)


class _FakeSQSClient:
    def __init__(self):
        self.calls: list[dict] = []

    def send_message(self, **kwargs):
        self.calls.append(kwargs)
        return {"MessageId": "fake-id"}


def _make_user_event() -> NotificationEvent:
    return NotificationEvent.make_user_untreatable(
        service="ff-ingestion",
        environment="prod",
        failure_code=FailureCode.PARSE_FAILED,
        template_id="user.untreatable.parse_failed",
        sender="u@x.com",
        source_message_id="<m1@x.com>",
    )


def _make_support_event() -> NotificationEvent:
    return NotificationEvent.make_support_alert(
        service="ff-ingestion",
        environment="prod",
        failure_code=FailureCode.POISON_MESSAGE,
        template_id="support.poison_message",
        error=ErrorInfo(class_="X", message="boom"),
        source_message_id="<m1@x.com>",
    )


@pytest.fixture
def publisher(monkeypatch):
    client = _FakeSQSClient()

    class _FakeBoto3:
        @staticmethod
        def client(*_a, **_kw):
            return client

        @staticmethod
        def Session(*_a, **_kw):
            class _S:
                @staticmethod
                def client(*_a2, **_kw2):
                    return client

            return _S()

    monkeypatch.setattr(
        "infrastructure.messaging.sqs_notification_publisher.boto3", _FakeBoto3
    )
    monkeypatch.setattr(
        "infrastructure.messaging.sqs_notification_publisher.settings.sqs_notifications_queue_url",
        "https://sqs.fake/notifications",
    )
    monkeypatch.setattr(
        "infrastructure.messaging.sqs_notification_publisher.settings.notifications_enabled",
        True,
    )
    pub = SQSNotificationPublisher()
    return pub, client


@pytest.mark.asyncio
async def test_publish_user_event_sends_payload_and_attributes(publisher):
    pub, client = publisher
    event = _make_user_event()
    await pub.publish(event)

    assert len(client.calls) == 1
    call = client.calls[0]
    body = json.loads(call["MessageBody"])
    assert body["category"] == "user_untreatable"
    assert body["recipient"]["email"] == "u@x.com"
    attrs = call["MessageAttributes"]
    assert attrs["category"]["StringValue"] == "user_untreatable"
    assert attrs["failure_code"]["StringValue"] == "PARSE_FAILED"
    assert attrs["schema_version"]["StringValue"] == "1"


@pytest.mark.asyncio
async def test_publish_skips_when_disabled(publisher, monkeypatch):
    pub, client = publisher
    monkeypatch.setattr(
        "infrastructure.messaging.sqs_notification_publisher.settings.notifications_enabled",
        False,
    )
    await pub.publish(_make_user_event())
    assert client.calls == []


@pytest.mark.asyncio
async def test_publish_skips_when_queue_url_missing(publisher, monkeypatch):
    pub, client = publisher
    monkeypatch.setattr(
        "infrastructure.messaging.sqs_notification_publisher.settings.sqs_notifications_queue_url",
        "",
    )
    await pub.publish(_make_user_event())
    assert client.calls == []


@pytest.mark.asyncio
async def test_publish_fifo_uses_event_id_as_dedup(monkeypatch):
    client = _FakeSQSClient()

    class _FakeBoto3:
        @staticmethod
        def client(*_a, **_kw):
            return client

        @staticmethod
        def Session(*_a, **_kw):
            class _S:
                @staticmethod
                def client(*_a2, **_kw2):
                    return client

            return _S()

    monkeypatch.setattr(
        "infrastructure.messaging.sqs_notification_publisher.boto3", _FakeBoto3
    )
    monkeypatch.setattr(
        "infrastructure.messaging.sqs_notification_publisher.settings.sqs_notifications_queue_url",
        "https://sqs.fake/notifications.fifo",
    )
    monkeypatch.setattr(
        "infrastructure.messaging.sqs_notification_publisher.settings.notifications_enabled",
        True,
    )
    pub = SQSNotificationPublisher()
    event = _make_support_event()
    await pub.publish(event)

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["MessageGroupId"] == "support_alert"
    assert call["MessageDeduplicationId"] == event.event_id
