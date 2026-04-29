from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from domain.enums.parsing_status import ParsingStatus
from domain.value_objects.email_metadata import EmailThreadMetadata


@dataclass
class FareEvent:
    """
    Entité métier publiée downstream.

    Forme volontairement alignée sur l’output de `ff-ingestion`:
    - id: UUID string
    - sender, parsed_at (ISO), email_body_length, status
    - subject, extracted_travel (dict), openai_response_id, failure_reasons
    - message_id/in_reply_to/references/reply_to (threading)
    """

    id: str
    sender: str
    parsed_at: str
    email_body_length: int
    status: str

    subject: str | None = None
    extracted_travel: dict | None = None
    openai_response_id: str | None = None
    failure_reasons: list[str] | None = None

    message_id: str | None = None
    in_reply_to: str | None = None
    references: str | None = None
    reply_to: str | None = None

    @staticmethod
    def create(
        *,
        sender: str,
        email_body_length: int,
        status: ParsingStatus | str = ParsingStatus.parsed,
        subject: str | None = None,
        extracted_travel: dict | None = None,
        openai_response_id: str | None = None,
        failure_reasons: list[str] | None = None,
        thread: EmailThreadMetadata | None = None,
    ) -> FareEvent:
        """
        Factory: construit un `FareEvent` conforme.

        Utilisé par
        ---------
        - `application.use_cases.ParseEmailUseCase`

        Utilise
        -------
        - `uuid.uuid4` (id)
        - `datetime.now(UTC).isoformat()` (parsed_at)
        - `EmailThreadMetadata` si fourni

        Impact
        ------
        - Aucun side-effect externe; pure construction de données.
        """
        # Parité `ff-ingestion`: `status` doit être "parsed" ou "parsing_failed".
        # `str(Enum)` donne "ParsingStatus.parsing_failed", donc on normalise.
        normalized_status = (
            status.value if isinstance(status, ParsingStatus) else str(status)
        )
        return FareEvent(
            id=str(uuid.uuid4()),
            sender=sender,
            parsed_at=datetime.now(UTC).isoformat(),
            email_body_length=email_body_length,
            status=normalized_status,
            subject=subject,
            extracted_travel=extracted_travel,
            openai_response_id=openai_response_id,
            failure_reasons=failure_reasons,
            message_id=thread.message_id if thread else None,
            in_reply_to=thread.in_reply_to if thread else None,
            references=thread.references if thread else None,
            reply_to=thread.reply_to if thread else None,
        )

    def to_dict(self) -> dict:
        """
        Convertit l’entité en dict JSON-sérialisable.

        Utilisé par
        ---------
        - `ParseEmailUseCase.execute` (retour API/consumer)
        - `SQSPublisher` (MessageBody)
        """
        return asdict(self)
