from __future__ import annotations

import json

import pytest

from infrastructure.messaging.sqs_publisher import SQSPublisher


class _FakeSQSClient:
    def __init__(self):
        self.calls: list[dict] = []

    def send_message(self, **kwargs):
        self.calls.append(kwargs)
        return {"MessageId": "fake-id"}


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
        "infrastructure.messaging.sqs_publisher.boto3", _FakeBoto3
    )
    monkeypatch.setattr(
        "infrastructure.messaging.sqs_publisher.settings.sqs_fare_event_queue_url",
        "https://sqs.fake/queue",
    )
    pub = SQSPublisher()
    return pub, client


@pytest.mark.asyncio
async def test_publish_fare_event_sends_message(publisher):
    pub, client = publisher
    fare = {"id": "fe-1", "sender": "a@b.com"}
    await pub.publish_fare_event(fare)

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["QueueUrl"] == "https://sqs.fake/queue"
    assert json.loads(call["MessageBody"]) == fare


@pytest.mark.asyncio
async def test_publish_fare_event_skips_when_queue_url_not_configured(
    monkeypatch, publisher
):
    pub, client = publisher
    monkeypatch.setattr(
        "infrastructure.messaging.sqs_publisher.settings.sqs_fare_event_queue_url",
        "",
    )
    await pub.publish_fare_event({"id": "fe-1"})
    assert client.calls == []


@pytest.mark.asyncio
async def test_publish_fare_event_fifo_adds_group_and_dedup(monkeypatch):
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
        "infrastructure.messaging.sqs_publisher.boto3", _FakeBoto3
    )
    monkeypatch.setattr(
        "infrastructure.messaging.sqs_publisher.settings.sqs_fare_event_queue_url",
        "https://sqs.fake/queue.fifo",
    )
    monkeypatch.setattr(
        "infrastructure.messaging.sqs_publisher.settings.parsed_sqs_message_group_id",
        "grp-1",
    )

    pub = SQSPublisher()
    await pub.publish_fare_event({"id": "fe-deterministic"})

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["MessageGroupId"] == "grp-1"
    assert call["MessageDeduplicationId"] == "fe-deterministic"
