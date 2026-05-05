from __future__ import annotations

import base64
import json

import pytest

from infrastructure.messaging.sqs_consumer import SQSConsumer, _safe_int


def test_safe_int_handles_bad_values():
    assert _safe_int(None, default=1) == 1
    assert _safe_int("abc", default=1) == 1
    assert _safe_int("3", default=0) == 3
    assert _safe_int(5, default=0) == 5


# ─────────────────────────────────────────────
# _parse_message_body / _unwrap_ses
# ─────────────────────────────────────────────
class _FakeIngestionService:
    parse_email_use_case = None
    process_email_use_case = None
    notify_failure_use_case = None


@pytest.fixture
def consumer(monkeypatch):
    """Crée un consumer sans toucher à AWS (boto3 mocké)."""

    class _FakeBoto3:
        @staticmethod
        def client(*_args, **_kwargs):
            return object()

        @staticmethod
        def Session(*_args, **_kwargs):
            class _S:
                @staticmethod
                def client(*_a, **_kw):
                    return object()

            return _S()

    monkeypatch.setattr(
        "infrastructure.messaging.sqs_consumer.boto3", _FakeBoto3
    )
    return SQSConsumer(ingestion_service=_FakeIngestionService())


def test_parse_message_body_empty(consumer):
    assert consumer._parse_message_body(None) == {}
    assert consumer._parse_message_body("") == {}


def test_parse_message_body_invalid_json_returns_empty(consumer):
    assert consumer._parse_message_body("not json") == {}


def test_parse_message_body_plain_json(consumer):
    body = json.dumps({"sender": "a@b.com", "email_body": "hello"})
    out = consumer._parse_message_body(body)
    assert out["sender"] == "a@b.com"
    assert out["email_body"] == "hello"


def test_parse_message_body_sns_wrapper_passthrough(consumer):
    inner = json.dumps({"sender": "x@y.com", "email_body": "abc"})
    body = json.dumps({"Message": inner})
    out = consumer._parse_message_body(body)
    assert out["sender"] == "x@y.com"


def test_parse_message_body_ses_unwraps_and_decodes_base64(consumer):
    raw_email = "From: alice@x.com\r\nSubject: Trip\r\n\r\nHello"
    inner = {
        "mail": {
            "source": "alice@x.com",
            "messageId": "<msg-1@x>",
            "commonHeaders": {"subject": "Trip"},
            "headers": [],
        },
        "content": base64.b64encode(raw_email.encode()).decode(),
    }
    body = json.dumps({"Message": json.dumps(inner)})
    out = consumer._parse_message_body(body)
    assert out["sender"] == "alice@x.com"
    assert out["subject"] == "Trip"
    assert out["message_id"] == "<msg-1@x>"
    assert "Hello" in out["email_body"]


def test_parse_message_body_ses_invalid_base64_falls_back(consumer):
    inner = {
        "mail": {
            "source": "alice@x.com",
            "messageId": None,
            "commonHeaders": {},
            "headers": [],
        },
        "content": "not-base64!!",
    }
    body = json.dumps({"Message": json.dumps(inner)})
    out = consumer._parse_message_body(body)
    # Le fallback retourne la string brute (pas d'exception)
    assert out["email_body"] == "not-base64!!"


def test_parse_message_body_ses_extracts_threading_headers(consumer):
    inner = {
        "mail": {
            "source": "a@x.com",
            "messageId": None,
            "commonHeaders": {},
            "headers": [
                {"name": "In-Reply-To", "value": "<irt@x>"},
                {"name": "References", "value": "<r1@x> <r2@x>"},
                {"name": "Reply-To", "value": "reply@x.com"},
                {"name": "Message-ID", "value": "<mid@x>"},
            ],
        },
        "content": base64.b64encode(b"Hi").decode(),
    }
    body = json.dumps({"Message": json.dumps(inner)})
    out = consumer._parse_message_body(body)
    assert out["in_reply_to"] == "<irt@x>"
    assert out["references"] == "<r1@x> <r2@x>"
    assert out["reply_to"] == "reply@x.com"
    assert out["message_id"] == "<mid@x>"
