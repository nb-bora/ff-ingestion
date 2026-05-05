"""
Use-case Application: traitement complet d'un email côté consumer.

Rôle
----
- Orchestrer la chaîne complète de traitement d'un email entrant:
  1. Parse via `ParseEmailUseCase` (produit un `FareEvent`)
  2. Validation post-parse (non bloquante) via `FareEventSchema`
  3. Publication sur la queue downstream via `IMessagePublisher`

Pourquoi ici (et pas dans le consumer) ?
----------------------------------------
- Le consumer (Infrastructure) ne doit faire que:
  - polling, unwrap SNS/SES, delete, heartbeat, segment X-Ray
- L'orchestration métier (parse → validate → publish) est une responsabilité
  Application, isolable et testable sans AWS.

Utilisé par
---------
- `infrastructure.messaging.SQSConsumer` (un appel par message reçu)

Erreurs
-------
- `MissingSenderError` levée si sender absent (utile au consumer pour
  prendre la décision de delete sans retry).
- Toute autre exception (OpenAI, publish, etc.) remonte au consumer
  qui décide du retry/DLQ.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from application.interfaces.message_publisher import IMessagePublisher
from application.use_cases.parse_email_use_case import ParseEmailUseCase
from domain.entities.email_message import EmailMessage
from logger import logger
from presentation.schemas.fare_event_schema import FareEventSchema
from shared.exceptions import MissingSenderError


class ProcessEmailUseCase:
    """Use-case "consumer": parse + validation post-parse + publish."""

    def __init__(
        self,
        *,
        parse_email: ParseEmailUseCase,
        publisher: IMessagePublisher,
    ):
        self._parse_email = parse_email
        self._publisher = publisher

    async def execute(self, email: EmailMessage) -> dict:
        """
        Parse l'email, valide le `FareEvent` (non bloquant), publie, retourne.

        Étapes
        ------
        - Valide le sender (sinon `MissingSenderError` propagée)
        - Parse → `FareEvent` dict
        - Valide via `FareEventSchema` (log uniquement, pas bloquant)
        - Publie sur la queue downstream
        - Retourne le `FareEvent`
        """
        if not email.sender:
            raise MissingSenderError("No sender email found in message")

        fare_event = await self._parse_email.execute(email)
        _validate_non_blocking(fare_event)
        await self._publisher.publish_fare_event(fare_event)
        return fare_event


def _validate_non_blocking(fare_event: dict) -> None:
    """Valide le `FareEvent` selon le schéma. Log uniquement, n'élève pas."""
    try:
        _ = FareEventSchema.model_validate(fare_event)
    except ValidationError as e:
        logger.error(
            "FareEventSchema validation failed (non-blocking): "
            "fare_event_id=%s errors=%s payload=%s",
            fare_event.get("id"),
            e.errors(),
            json.dumps(fare_event, ensure_ascii=False)[:500],
        )
