"""
Publication des `FareEvent` vers SQS (queue downstream).

Rôle
----
- Implémenter `application.interfaces.IMessagePublisher` en utilisant boto3 SQS.
- Propager le trace header X-Ray (`X-Amzn-Trace-Id`) dans `MessageAttributes`
  quand un segment est actif.
- Gérer le format FIFO (MessageGroupId / MessageDeduplicationId) si la queue
  cible est `.fifo`.

Utilise
-------
- `config.settings` (queue URL, région, profil, group id)
- `xray_config.current_trace_header` (propagation tracing)
- `boto3` (SQS client)

Impact / effets de bord
----------------------
- Envoie un message SQS sur `settings.sqs_fare_event_queue_url`.
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config

from application.interfaces.message_publisher import IMessagePublisher
from config import settings
from logger import logger
from presentation.api.metrics import publish_total
from xray_config import current_trace_header

_BOTO_CONFIG = Config(
    connect_timeout=5,
    read_timeout=10,
    retries={"max_attempts": 3, "mode": "standard"},
)


class SQSPublisher(IMessagePublisher):
    """
    Publisher SQS des `FareEvent`.

    Design
    ------
    - Les appels boto3 sont exécutés dans un `ThreadPoolExecutor` pour ne pas
      bloquer la boucle asyncio (boto3 est synchrone).
    - Détection automatique des queues FIFO (suffixe `.fifo`) pour ajouter
      `MessageGroupId` et `MessageDeduplicationId` (à partir de l'`id` du
      `FareEvent` quand il est déterministe).
    """

    def __init__(self):
        if settings.aws_profile:
            session = boto3.Session(profile_name=settings.aws_profile)
            self._sqs = session.client(
                "sqs", region_name=settings.aws_region, config=_BOTO_CONFIG
            )
            logger.debug("SQSPublisher: profil AWS=%s", settings.aws_profile)
        else:
            self._sqs = boto3.client(
                "sqs", region_name=settings.aws_region, config=_BOTO_CONFIG
            )
            logger.debug("SQSPublisher: credentials AWS par défaut")

        self._executor = ThreadPoolExecutor(max_workers=2)
        self._is_fifo = (settings.sqs_fare_event_queue_url or "").endswith(".fifo")

    def _send(self, fare_event: dict, trace_header: str | None) -> dict:
        """
        Envoi synchrone boto3.

        Retour
        ------
        Le dict de réponse boto3 (contenant `MessageId`, etc.).
        """
        kwargs: dict = {
            "QueueUrl": settings.sqs_fare_event_queue_url,
            "MessageBody": json.dumps(fare_event),
        }
        if trace_header:
            kwargs["MessageAttributes"] = {
                "X-Amzn-Trace-Id": {
                    "DataType": "String",
                    "StringValue": trace_header,
                }
            }
        if self._is_fifo:
            kwargs["MessageGroupId"] = settings.parsed_sqs_message_group_id
            # Idempotence: si l'`id` du fare_event est déterministe (uuid5),
            # SQS dédupliquera nativement les republications.
            dedup_id = fare_event.get("id")
            if dedup_id:
                kwargs["MessageDeduplicationId"] = str(dedup_id)
        return self._sqs.send_message(**kwargs)

    async def publish_fare_event(self, fare_event: dict) -> None:
        """Publie un `FareEvent` sur la queue downstream."""
        if not settings.sqs_fare_event_queue_url:
            logger.warning(
                "SQS_FARE_EVENT_QUEUE_URL non configuré, FareEvent non publié: id=%s",
                fare_event.get("id"),
            )
            publish_total.labels(outcome="not_configured").inc()
            return

        trace_header = current_trace_header()
        loop = asyncio.get_running_loop()
        try:
            response = await loop.run_in_executor(
                self._executor, lambda: self._send(fare_event, trace_header)
            )
        except Exception:
            publish_total.labels(outcome="error").inc()
            raise

        publish_total.labels(outcome="success").inc()
        logger.info(
            "Published FareEvent: id=%s sqs_message_id=%s",
            fare_event.get("id"),
            response.get("MessageId") if isinstance(response, dict) else None,
        )
