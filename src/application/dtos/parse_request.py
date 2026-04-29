from __future__ import annotations

from pydantic import BaseModel


class ParseRequestDTO(BaseModel):
    email_body: str
    sender: str | None = None
