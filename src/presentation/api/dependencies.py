"""
Fournisseurs de dépendances (composition root) pour la couche Presentation.

Objectif
--------
- Centraliser l'instanciation des objets "singleton" (metrics, service, consumer).
- Garantir que l'API et le consumer partagent les mêmes instances (parser/publisher),
  tout en restant testable (override via `app.dependency_overrides`).

Utilisé par
---------
- `src/main.py` via `get_sqs_consumer()`
- `presentation.api.ingestion_router` via `get_metrics()` et `get_ingestion_service()`

Impact
------
- `lru_cache` rend ces dépendances globales par process (équivalent "singleton").
- Pour les tests: utiliser `app.dependency_overrides[...] = lambda: ...` ou
  appeler `.cache_clear()` sur les getters.
"""

from __future__ import annotations

from functools import lru_cache

from application.services.ingestion_service import IngestionService
from infrastructure.messaging.sqs_consumer import SQSConsumer
from infrastructure.messaging.sqs_notification_publisher import (
    SQSNotificationPublisher,
)
from infrastructure.messaging.sqs_publisher import SQSPublisher
from infrastructure.parsers.openai_email_parser import OpenAIEmailParser
from presentation.api.metrics import (
    Metrics,
    notification_publish_total,
    notification_throttled_total,
)


@lru_cache(maxsize=1)
def get_metrics() -> Metrics:
    """Retourne l'instance singleton de `Metrics`."""
    return Metrics()


@lru_cache(maxsize=1)
def get_publisher() -> SQSPublisher:
    """Retourne l'instance singleton de publisher SQS (FareEvent)."""
    return SQSPublisher()


@lru_cache(maxsize=1)
def get_notification_publisher() -> SQSNotificationPublisher:
    """Retourne l'instance singleton de publisher SQS (notifications)."""
    return SQSNotificationPublisher()


@lru_cache(maxsize=1)
def get_ingestion_service() -> IngestionService:
    """Construit l'orchestrateur Application (`IngestionService`)."""
    parser = OpenAIEmailParser()
    publisher = get_publisher()
    notif_publisher = get_notification_publisher()
    return IngestionService.build(
        parser=parser,
        publisher=publisher,
        notification_publisher=notif_publisher,
        metrics_publish=notification_publish_total,
        metrics_throttled=notification_throttled_total,
    )


@lru_cache(maxsize=1)
def get_sqs_consumer() -> SQSConsumer:
    """Construit le consumer SQS (singleton) branché sur `IngestionService`."""
    return SQSConsumer(ingestion_service=get_ingestion_service())
