"""
Fournisseurs de dépendances (composition root) pour la couche Presentation.

Objectif
--------
- Centraliser l’instanciation des objets "singleton" (metrics, service, consumer).
- Garantir que l’API et le consumer partagent les mêmes instances (parser/publisher),
  tout en restant testable (monkeypatch possible sur les getters).

Utilisé par
---------
- `src/main.py` via `get_sqs_consumer()`
- `presentation.api.ingestion_router` via `get_metrics()` et `get_ingestion_service()`

Impact
------
- `lru_cache` rend ces dépendances globales par process (équivalent "singleton").
"""

from __future__ import annotations

from functools import lru_cache

from application.services.ingestion_service import IngestionService
from infrastructure.messaging.sqs_consumer import SQSConsumer
from infrastructure.messaging.sqs_publisher import SQSPublisher
from infrastructure.parsers.openai_email_parser import OpenAIEmailParser


class Metrics:
    """
    Collecteur de métriques in-memory.

    Champs
    ------
    - `messages_processed`: incrémenté quand un parse API réussit.
    - `errors`: incrémenté pour les erreurs API.

    Utilisé par
    ---------
    - `presentation.api.ingestion_router.health_check`
    - `presentation.api.ingestion_router.get_metrics_endpoint`
    - `presentation.api.ingestion_router.parse_airfare`

    Note
    ----
    Parité `ff-ingestion`: ces métriques ne couvrent pas le consumer SQS (qui a
    sa propre logique de log/erreurs).
    """

    def __init__(self):
        self.messages_processed = 0
        self.errors = 0

    def increment_processed(self) -> None:
        self.messages_processed += 1

    def increment_error(self) -> None:
        self.errors += 1


@lru_cache(maxsize=1)
def get_metrics() -> Metrics:
    """
    Retourne l’instance singleton de `Metrics`.

    Utilise
    -------
    - `functools.lru_cache` pour stabiliser l’instance.
    """
    return Metrics()


@lru_cache(maxsize=1)
def get_ingestion_service() -> IngestionService:
    """
    Construit l’orchestrateur Application (`IngestionService`) et ses dépendances.

    Utilise
    -------
    - `OpenAIEmailParser` (impl de `IEmailParser`)
    - `SQSPublisher` (impl de `IMessagePublisher`)
    - `IngestionService.build` pour assembler les use-cases

    Impact
    ------
    - Instancie des clients (OpenAI/boto3) indirectement.
    """
    parser = OpenAIEmailParser()
    return IngestionService.build(parser=parser)


@lru_cache(maxsize=1)
def get_publisher() -> SQSPublisher:
    """Retourne l’instance singleton de publisher SQS."""
    return SQSPublisher()


@lru_cache(maxsize=1)
def get_sqs_consumer() -> SQSConsumer:
    """
    Construit le consumer SQS (singleton) branché sur `IngestionService`.

    Utilisé par
    ---------
    - `main.lifespan`
    """
    return SQSConsumer(
        ingestion_service=get_ingestion_service(),
        publisher=get_publisher(),
    )
