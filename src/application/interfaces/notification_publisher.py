"""
Contrat Application: publication d'un `NotificationEvent` sur la queue
notifications dédiée (consommée par `ff-notifier`).

Utilisé par
---------
- `application.use_cases.NotifyFailureUseCase`

Implémentations
--------------
- `infrastructure.messaging.sqs_notification_publisher.SQSNotificationPublisher`

Garanties attendues
-------------------
- Publication best-effort : l'implémentation peut lever en cas d'erreur
  réseau/AWS ; le caller (`NotifyFailureUseCase`) absorbe ces exceptions
  pour ne **jamais** propager au flux principal d'ingestion.
- Idempotence applicative : `event.event_id` est déterministe (uuid5) côté
  Domain ; les implémentations FIFO peuvent l'utiliser comme
  `MessageDeduplicationId`.
"""

from __future__ import annotations

from typing import Protocol

from domain.entities.notification_event import NotificationEvent


class INotificationPublisher(Protocol):
    async def publish(self, event: NotificationEvent) -> None:
        """
        Publie l'événement sur la queue notifications.

        Impact
        ------
        - Side-effect (réseau) selon l'implémentation.
        - Peut lever sur erreur réseau/AWS — à absorber par le caller.
        """
