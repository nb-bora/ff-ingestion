from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TravelExtract(BaseModel):
    """
    VO de sortie OpenAI (forme alignée sur ff-ingestion).

    Note: ce VO est utilisé pour valider/structurer l’extraction, mais l’event
    publie un dict JSON sérialisable (comme ff-ingestion).
    """

    trip_type: Literal["one_way", "round_trip"] | None = None
    origin: str | None = None
    destination: str | None = None
    departure_date: str | None = None
    return_date: str | None = None

    passengers: int | None = None
    passengers_adults: int | None = None
    passengers_children: int | None = None

    budget_amount: float | None = None
    budget_currency: str | None = None

    cabin_class: Literal["economy", "premium_economy", "business", "first"] | None = (
        None
    )

    missing_fields: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] | None = None
