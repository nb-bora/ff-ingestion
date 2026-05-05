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

Notifications
-------------
- En cas de `ParseError` (parsing impossible côté OpenAI), on émet une
  `user_untreatable` via `NotifyFailureUseCase` puis on **re-raise** :
  - le caller (consumer) décide du delete/redelivery
  - la notif est best-effort (`NotifyFailureUseCase` n'élève jamais)

Utilisé par
---------
- `infrastructure.messaging.SQSConsumer` (un appel par message reçu)

Erreurs
-------
- `MissingSenderError` levée si sender absent (utile au consumer pour
  prendre la décision de delete sans retry).
- `ParseError` re-raised après publication d'une `user_untreatable`.
- Toute autre exception (OpenAI, publish, etc.) remonte au consumer
  qui décide du retry/DLQ.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from application.interfaces.message_publisher import IMessagePublisher
from application.use_cases.notify_failure_use_case import NotifyFailureUseCase
from application.use_cases.parse_email_use_case import ParseEmailUseCase
from domain.entities.email_message import EmailMessage
from domain.enums.failure_code import FailureCode
from logger import logger
from presentation.schemas.fare_event_schema import FareEventSchema
from shared.exceptions import MissingSenderError, ParseError

_DEFAULT_PARSE_FAILED_SUMMARY = (
    "Nous n'avons pas réussi à interpréter votre demande automatiquement."
)


class ProcessEmailUseCase:
    """Use-case "consumer": parse + validation post-parse + publish."""

    def __init__(
        self,
        *,
        parse_email: ParseEmailUseCase,
        publisher: IMessagePublisher,
        notify_failure: NotifyFailureUseCase,
    ):
        self._parse_email = parse_email
        self._publisher = publisher
        self._notify_failure = notify_failure

    async def execute(self, email: EmailMessage) -> dict:
        """
        Parse l'email, route selon Tier 1, publie/notify, retourne.

        Étapes
        ------
        - Valide le sender (sinon `MissingSenderError` propagée)
        - Parse → `FareEvent` dict
            - en cas de `ParseError` : émet une `user_untreatable` puis re-raise
        - Si `status != parsed` : notifie (queue notifications) et NE publie PAS
          sur la queue fare-event
        - Sinon: valide via `FareEventSchema` (log uniquement, pas bloquant)
          puis publie sur la queue downstream
        - Retourne le `FareEvent`
        """
        if not email.sender:
            raise MissingSenderError("No sender email found in message")

        try:
            fare_event = await self._parse_email.execute(email)
        except ParseError:
            await self._notify_failure.user_untreatable(
                email=email,
                code=FailureCode.PARSE_FAILED,
                human_summary=(
                    "Nous n'avons pas réussi à interpréter votre demande "
                    "automatiquement."
                ),
            )
            raise

        # Workflow contractuel: seuls les events "parsed" partent sur la queue
        # downstream; le reste part en notifications user_untreatable.
        if (fare_event or {}).get("status") != "parsed":
            await self._notify_failure.user_untreatable(
                email=email,
                code=FailureCode.PARSE_FAILED,
                fare_event=fare_event,
                human_summary=_human_summary_from_fare_event(fare_event),
            )
            return fare_event

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


def _human_summary_from_fare_event(fare_event: dict | None) -> str:
    """Construit un résumé user-friendly depuis `failure_reasons` si présent."""
    if not isinstance(fare_event, dict):
        return _DEFAULT_PARSE_FAILED_SUMMARY
    reasons = fare_event.get("failure_reasons")
    if isinstance(reasons, list) and reasons:
        # Garder court et robuste; le notifier affichera aussi les missing_fields.
        return " ".join(str(r) for r in reasons if r)[:300] or (
            _DEFAULT_PARSE_FAILED_SUMMARY
        )
    return _DEFAULT_PARSE_FAILED_SUMMARY
