from __future__ import annotations

from pydantic import BaseModel


class ParseRequestSchema(BaseModel):
    email_body: str
    sender: str | None = None
