from __future__ import annotations

from dataclasses import dataclass

from domain.value_objects.email_metadata import EmailThreadMetadata


@dataclass(frozen=True)
class EmailMessage:
    """
    Entité représentant un email entrant.

    Peut venir:
    - de l’API (`/parse`) => corps déjà "texte"
    - de SQS (SNS/SES) => payload décodé + métadonnées SES
    """

    sender: str
    subject: str | None
    body_text: str
    thread: EmailThreadMetadata = EmailThreadMetadata()
