"""
Publication des `NotificationEvent` vers la queue SQS notifications.

Rôle
----
- Implémenter `application.interfaces.INotificationPublisher` en utilisant boto3 SQS.
- Cibler `settings.sqs_notifications_queue_url` (queue dédiée consommée par
  `ff-notifier`).
- Propager le trace header X-Ray (`X-Amzn-Trace-Id`) et exposer la `category`
  + `failure_code` via `MessageAttributes` pour faciliter le filtrage.
- Gérer le format FIFO (MessageGroupId / MessageDeduplicationId) si la queue
  cible se termine par `.fifo`.
- Honore le kill-switch `settings.notifications_enabled`.

Garanties
---------
- `event.event_id` étant déterministe (uuid5), il sert de
  `MessageDeduplicationId` en FIFO → idempotence native côté SQS.
- En cas d'erreur AWS, on lève (le caller `NotifyFailureUseCase` absorbe).
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config

from application.interfaces.notification_publisher import INotificationPublisher
from config import settings
from domain.entities.notification_event import NotificationEvent
from logger import logger
from presentation.api.metrics import notification_publish_total
from xray_config import current_trace_header

_BOTO_CONFIG = Config(
    connect_timeout=5,
    read_timeout=10,
    retries={"max_attempts": 3, "mode": "standard"},
)


class SQSNotificationPublisher(INotificationPublisher):
    """Publisher SQS des `NotificationEvent`."""

    def __init__(self):
        if settings.aws_profile:
            session = boto3.Session(profile_name=settings.aws_profile)
            self._sqs = session.client(
                "sqs", region_name=settings.aws_region, config=_BOTO_CONFIG
            )
            logger.debug(
                "SQSNotificationPublisher: profil AWS=%s", settings.aws_profile
            )
        else:
            self._sqs = boto3.client(
                "sqs", region_name=settings.aws_region, config=_BOTO_CONFIG
            )
            logger.debug("SQSNotificationPublisher: credentials AWS par défaut")

        self._executor = ThreadPoolExecutor(max_workers=2)
        queue_url = getattr(settings, "sqs_notifications_queue_url", "") or ""
        self._is_fifo = queue_url.endswith(".fifo")

    def _send(self, event_dict: dict, trace_header: str | None) -> dict:
        """Envoi synchrone boto3."""
        queue_url = settings.sqs_notifications_queue_url
        kwargs: dict = {
            "QueueUrl": queue_url,
            "MessageBody": json.dumps(event_dict, ensure_ascii=False),
        }
        attrs: dict[str, dict] = {
            "category": {
                "DataType": "String",
                "StringValue": event_dict.get("category", "unknown"),
            },
            "failure_code": {
                "DataType": "String",
                "StringValue": event_dict.get("failure_code", "unknown"),
            },
            "schema_version": {
                "DataType": "Number",
                "StringValue": str(event_dict.get("schema_version", 1)),
            },
        }
        if trace_header:
            attrs["X-Amzn-Trace-Id"] = {
                "DataType": "String",
                "StringValue": trace_header,
            }
        kwargs["MessageAttributes"] = attrs

        if self._is_fifo:
            kwargs["MessageGroupId"] = event_dict.get("category") or "notifications"
            dedup = event_dict.get("event_id")
            if dedup:
                kwargs["MessageDeduplicationId"] = str(dedup)

        return self._sqs.send_message(**kwargs)

    async def publish(self, event: NotificationEvent) -> None:
        """Publie un `NotificationEvent` sur la queue notifications."""
        if not getattr(settings, "notifications_enabled", True):
            logger.debug(
                "Notifications disabled, skipping publish: event_id=%s",
                event.event_id,
            )
            notification_publish_total.labels(
                category=event.category.value, outcome="disabled"
            ).inc()
            return

        queue_url = getattr(settings, "sqs_notifications_queue_url", "")
        if not queue_url:
            logger.warning(
                "SQS_NOTIFICATIONS_QUEUE_URL non configuré, NotificationEvent "
                "non publié: event_id=%s",
                event.event_id,
            )
            notification_publish_total.labels(
                category=event.category.value, outcome="not_configured"
            ).inc()
            return

        trace_header = current_trace_header()
        event_dict = event.to_dict()
        loop = asyncio.get_running_loop()
        try:
            response = await loop.run_in_executor(
                self._executor, lambda: self._send(event_dict, trace_header)
            )
        except Exception:
            notification_publish_total.labels(
                category=event.category.value, outcome="error"
            ).inc()
            raise

        notification_publish_total.labels(
            category=event.category.value, outcome="success"
        ).inc()
        logger.info(
            "Published NotificationEvent: event_id=%s category=%s code=%s "
            "sqs_message_id=%s",
            event.event_id,
            event.category.value,
            event.failure_code.value,
            response.get("MessageId") if isinstance(response, dict) else None,
        )
