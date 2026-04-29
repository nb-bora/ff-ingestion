from __future__ import annotations

from pydantic import BaseModel


class ParseResponseDTO(BaseModel):
    fare_event_id: str | None = None
    status: str
