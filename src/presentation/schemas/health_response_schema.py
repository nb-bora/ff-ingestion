from __future__ import annotations

from pydantic import BaseModel


class HealthResponseSchema(BaseModel):
    status: str
    service: str
    version: str
    environment: str
    consumer_enabled: bool
    aws_region: str
    aws_profile: str
    openai_configured: bool
    metrics: dict
