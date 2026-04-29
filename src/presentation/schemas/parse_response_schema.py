from __future__ import annotations

from pydantic import BaseModel


class ParseResponseSchema(BaseModel):
    fare_event_id: str | None = None
    status: str
