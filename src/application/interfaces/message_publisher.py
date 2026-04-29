"""
Contrat Application: publication d’un message `FareEvent` (dict).

Utilisé par
---------
- `application.use_cases.ProcessEmailUseCase`

Implémentations
--------------
- `infrastructure.messaging.sqs_publisher.SQSPublisher`
"""

from __future__ import annotations

from typing import Protocol


class IMessagePublisher(Protocol):
    async def publish_fare_event(self, fare_event: dict) -> None:
        """
        Publie un `FareEvent` downstream.

        Impact
        ------
        - Side-effect (réseau) selon l’implémentation.
        """
