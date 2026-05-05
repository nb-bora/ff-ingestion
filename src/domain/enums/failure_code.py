"""
Enum unifié des codes d'échec publiés sur la queue notifications.

Rôle
----
Servir de pivot entre :
- les codes "Ingestion" (parse / consumer)
- les codes "Tier 1" (`ff-intelligence-engine`, mêmes valeurs textuelles que
  dans `src/rules/tier1_eligibility.py` côté intelligence engine)
- les codes "serveur" (poison messages, OpenAI down, etc.)

Cette unification permet à un même `NotificationEvent` schema d'être produit
indifféremment par Ingestion ou par ff-intelligence-engine.

Convention
----------
- Les codes Tier 1 commencent par `T1_R<id>_<MNEMO>` et ne doivent JAMAIS
  changer de valeur (contrat partagé documenté dans
  `docs/NOTIFICATIONS_CONTRACT.md`).
"""

from __future__ import annotations

from enum import Enum


class FailureCode(str, Enum):
    """Code canonique d'échec utilisé dans `NotificationEvent.failure_code`."""

    PARSE_FAILED = "PARSE_FAILED"
    MISSING_SENDER = "MISSING_SENDER"
    EMPTY_BODY = "EMPTY_BODY"
    POISON_MESSAGE = "POISON_MESSAGE"
    OPENAI_UNAVAILABLE = "OPENAI_UNAVAILABLE"
    UNKNOWN_INGESTION_ERROR = "UNKNOWN_INGESTION_ERROR"

    T1_R1_INVALID_ITINERARY = "T1_R1_INVALID_ITINERARY"
    T1_R1_SINGLE_ITINERARY_ROUNDTRIP = "T1_R1_SINGLE_ITINERARY_ROUNDTRIP"
    T1_R2_SEGMENTS_REQUIRED = "T1_R2_SEGMENTS_REQUIRED"
    T1_R3_CITY_DATE_REQUIRED = "T1_R3_CITY_DATE_REQUIRED"
    T1_R4_PRICE_REQUIRED = "T1_R4_PRICE_REQUIRED"
    T1_R5_CABIN_REQUIRED = "T1_R5_CABIN_REQUIRED"
    T1_R6_FULL_NAME_REQUIRED = "T1_R6_FULL_NAME_REQUIRED"
    T1_R8_SEGMENT_SEAT_AVAILABILITY = "T1_R8_SEGMENT_SEAT_AVAILABILITY"
    T1_R8_MISSING_SEAT_DATA = "T1_R8_MISSING_SEAT_DATA"
    T1_R9_MISSING_OPERATING_DETAILS = "T1_R9_MISSING_OPERATING_DETAILS"
    T1_R10_TICKETING_DATE = "T1_R10_TICKETING_DATE"

    @property
    def is_tier1(self) -> bool:
        """Indique si le code provient du Tier 1 (préfixe `T1_R`)."""
        return self.value.startswith("T1_R")
