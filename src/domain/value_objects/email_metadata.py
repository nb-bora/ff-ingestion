from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmailThreadMetadata:
    """
    Métadonnées de threading email (pour réponses downstream).
    Champs alignés sur ce que ff-ingestion met dans FareEvent.
    """

    message_id: str | None = None
    in_reply_to: str | None = None
    references: str | None = None
    reply_to: str | None = None
