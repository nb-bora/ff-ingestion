from __future__ import annotations

import os

from fastapi.testclient import TestClient

os.environ["CONSUMER_ENABLED"] = "false"

import main


def test_health_returns_status_and_checks():
    client = TestClient(main.app)
    resp = client.get("/health")
    body = resp.json()
    assert resp.status_code in (200, 503)
    assert body["status"] in ("healthy", "unhealthy")
    assert "service" in body
    assert "checks" in body
    assert "consumer_running" in body["checks"]
    assert "openai_configured" in body["checks"]


def test_metrics_endpoint_returns_prometheus_format():
    client = TestClient(main.app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")
    body = resp.text
    # Vérifie au moins la présence d'un compteur déclaré
    assert "ingestion_parse_total" in body or "# HELP" in body
