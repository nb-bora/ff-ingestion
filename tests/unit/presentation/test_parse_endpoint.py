from __future__ import annotations

import os

from fastapi.testclient import TestClient

os.environ["CONSUMER_ENABLED"] = "false"

import main
from presentation.api.dependencies import get_ingestion_service


class _FakeParseEmailUseCase:
    async def execute(self, _email):
        return {"id": "00000000-0000-0000-0000-000000000000"}


class _FakeService:
    parse_email_use_case = _FakeParseEmailUseCase()


def test_parse_empty_email_body_returns_400():
    client = TestClient(main.app)
    resp = client.post("/parse", json={"email_body": "   "})
    assert resp.status_code == 400
    assert resp.json()["error"] == "email_body cannot be empty"


def test_parse_missing_sender_returns_400():
    # keep real service but ensure behavior does not crash
    client = TestClient(main.app)
    resp = client.post("/parse", json={"email_body": "hello"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "Cannot extract sender email from message"


def test_parse_happy_path_returns_parsed():
    main.app.dependency_overrides[get_ingestion_service] = lambda: _FakeService()
    try:
        client = TestClient(main.app)
        resp = client.post(
            "/parse", json={"email_body": "hi", "sender": "a@b.com"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "parsed"
        assert body["fare_event_id"] == "00000000-0000-0000-0000-000000000000"
    finally:
        main.app.dependency_overrides.pop(get_ingestion_service, None)


def test_validation_error_is_400_not_422():
    client = TestClient(main.app)
    resp = client.post("/parse", json={})
    assert resp.status_code == 400
    assert "error" in resp.json()
