"""
Use-case Application: parsing d’un email en `FareEvent` (sans publication).

Rôle
----
- Appliquer les règles de parité `ff-ingestion` côté parsing:
  - sender obligatoire (sinon `MissingSenderError`)
  - extraction OpenAI via `IEmailParser`
  - calcul du `ParsingStatus` (parsed vs parsing_failed)
  - construction d’un `FareEvent` conforme (dict JSON sérialisable)

Utilisé par
---------
- `presentation.api.ingestion_router.parse_airfare` (chemin API)
- `application.use_cases.ProcessEmailUseCase` (chemin consumer)

Utilise
-------
- `domain.entities.FareEvent` (factory)
- `domain.enums.ParsingStatus`
- `application.interfaces.IEmailParser` (OpenAI ou autre)

Impact
------
- Aucun side-effect réseau direct (pas de SQS publish ici).
"""

from __future__ import annotations

from application.interfaces.email_parser import IEmailParser
from domain.entities.email_message import EmailMessage
from domain.entities.fare_event import FareEvent
from domain.enums.parsing_status import ParsingStatus
from shared.exceptions import MissingSenderError


class ParseEmailUseCase:
    """
    Parse un `EmailMessage` et retourne un `FareEvent` (dict).

    Important
    ---------
    Pour conserver la parité avec `ff-ingestion`, ce use-case ne publie pas:
    la publication est réalisée dans `ProcessEmailUseCase` (chemin consumer).
    """

    def __init__(self, *, parser: IEmailParser):
        """
        Paramètres
        ----------
        parser:
            Implémentation de `IEmailParser` (typiquement `OpenAIEmailParser`).
        """
        self._parser = parser

    async def execute(self, email: EmailMessage) -> dict:
        """
        Exécute le parsing.

        Erreurs
        ------
        - `MissingSenderError` si `email.sender` vide.
        """
        if not email.sender:
            raise MissingSenderError("No sender email found in message")

        (
            extracted_travel,
            openai_response_id,
            failure_reasons,
        ) = await self._parser.parse(email)

        status = (
            ParsingStatus.parsed
            if _is_valid_extraction(extracted_travel)
            else ParsingStatus.parsing_failed
        )

        fare_event = FareEvent.create(
            sender=email.sender,
            subject=email.subject,
            email_body_length=len(email.body_text or ""),
            status=status,
            extracted_travel=extracted_travel or {},
            openai_response_id=openai_response_id,
            failure_reasons=failure_reasons,
            thread=email.thread,
        ).to_dict()

        # Align ff-ingestion: API path doesn't publish; consumer publishes.
        return fare_event


def _is_valid_extraction(extracted_travel: dict | None) -> bool:
    """
    Aligné sur ff-ingestion: on exige au minimum origin + destination.
    (Le prompt inclut departure_date comme "required", mais le code de référence
    vérifie origin/destination en pratique.)

    Utilisé par
    ---------
    - `ParseEmailUseCase.execute` pour définir `ParsingStatus`

    Impact
    ------
    - Ne modifie pas l’extraction; pure validation.
    """
    if not extracted_travel or "error" in extracted_travel:
        return False
    origin = extracted_travel.get("origin")
    destination = extracted_travel.get("destination")
    has_origin = origin is not None and str(origin).strip() != ""
    has_destination = destination is not None and str(destination).strip() != ""
    return has_origin and has_destination
