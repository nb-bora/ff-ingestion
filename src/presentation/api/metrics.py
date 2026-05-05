"""
Métriques applicatives.

Rôle
----
- Exposer des compteurs / histogrammes Prometheus.
- Fournir une compat "in-memory" pour la rétro-compatibilité de l'API
  (`messages_processed`, `errors`).

Métriques exposées
------------------
- `ingestion_parse_total{outcome}` : compteur (parsed, failed, missing_sender)
- `ingestion_parse_duration_seconds` : histogramme de latence parse
- `ingestion_consumer_messages_total{outcome}` : compteur consumer
- `ingestion_consumer_inflight` : gauge messages en cours de traitement
- `ingestion_publish_total{outcome}` : compteur de publications

Endpoint
--------
- `GET /metrics` rend le texte Prometheus (content-type adapté).
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# ─────────────────────────────────────────────
# Definitions
# ─────────────────────────────────────────────
parse_total = Counter(
    "ingestion_parse_total",
    "Total number of API /parse calls by outcome",
    labelnames=("outcome",),
)
parse_duration = Histogram(
    "ingestion_parse_duration_seconds",
    "Latency of API /parse end-to-end",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
)

consumer_messages_total = Counter(
    "ingestion_consumer_messages_total",
    "Total number of SQS messages handled by outcome",
    labelnames=("outcome",),
)
consumer_inflight = Gauge(
    "ingestion_consumer_inflight",
    "Number of SQS messages currently in-flight",
)

publish_total = Counter(
    "ingestion_publish_total",
    "Total number of FareEvent publications by outcome",
    labelnames=("outcome",),
)


# ─────────────────────────────────────────────
# Compat in-memory (legacy)
# ─────────────────────────────────────────────
class Metrics:
    """
    Façade in-memory simple (rétro-compatibilité).

    Reflète les compteurs Prometheus pour les endpoints legacy.
    """

    def __init__(self):
        self._messages_processed = 0
        self._errors = 0

    @property
    def messages_processed(self) -> int:
        return self._messages_processed

    @property
    def errors(self) -> int:
        return self._errors

    def increment_processed(self) -> None:
        self._messages_processed += 1
        parse_total.labels(outcome="parsed").inc()

    def increment_error(self, *, outcome: str = "error") -> None:
        self._errors += 1
        parse_total.labels(outcome=outcome).inc()


# ─────────────────────────────────────────────
# Exposition
# ─────────────────────────────────────────────
def render_prometheus() -> tuple[bytes, str]:
    """
    Retourne `(payload, content_type)` pour exposer les métriques au format
    Prometheus.
    """
    return generate_latest(), CONTENT_TYPE_LATEST
