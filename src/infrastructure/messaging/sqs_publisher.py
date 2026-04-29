"""
Publication des `FareEvent` vers SQS (queue downstream).

Rôle
----
- Implémenter `application.interfaces.IMessagePublisher` en utilisant boto3 SQS.
- Propager le trace header X-Ray (`X-Amzn-Trace-Id`) dans `MessageAttributes`
  quand un segment est actif.

Utilisé par
---------
- `application.use_cases.ProcessEmailUseCase` (chemin consumer)

Utilise
-------
- `config.settings` (queue URL, région, profil)
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

from application.interfaces.message_publisher import IMessagePublisher
from config import settings
from logger import logger
from xray_config import current_trace_header


class SQSPublisher(IMessagePublisher):
    """
    Publisher SQS des `FareEvent`.

    Design
    ------
    - Les appels boto3 sont exécutés dans un `ThreadPoolExecutor` pour ne pas
      bloquer la boucle asyncio (boto3 est synchrone).
    """

    def __init__(self):
        """
        Initialise le client SQS.

        Utilise
        -------
        - `settings.aws_profile` (session explicite si profil fourni)
        - `settings.aws_region`
        """
        if settings.aws_profile:
            session = boto3.Session(profile_name=settings.aws_profile)
            self._sqs = session.client("sqs", region_name=settings.aws_region)
            logger.debug("SQSPublisher: profil AWS=%s", settings.aws_profile)
        else:
            self._sqs = boto3.client("sqs", region_name=settings.aws_region)
            logger.debug("SQSPublisher: credentials AWS par défaut")

        self._executor = ThreadPoolExecutor(max_workers=2)

    def _send(self, fare_event: dict, trace_header: str | None) -> None:
        """
        Envoi synchrone boto3.

        Utilisé par
        ---------
        - `publish_fare_event` via `run_in_executor`

        Impact
        ------
        - `send_message` vers la queue downstream.
        """
        kwargs: dict = {
            "QueueUrl": settings.sqs_fare_event_queue_url,
            "MessageBody": json.dumps(fare_event),
        }
        if trace_header:
            kwargs["MessageAttributes"] = {
                "X-Amzn-Trace-Id": {"DataType": "String", "StringValue": trace_header}
            }
        self._sqs.send_message(**kwargs)

    async def publish_fare_event(self, fare_event: dict) -> None:
        """
        Publie un `FareEvent` sur la queue downstream.

        Utilisé par
        ---------
        - `application.use_cases.ProcessEmailUseCase.execute`

        Utilise
        -------
        - `xray_config.current_trace_header`
        - `asyncio.get_event_loop().run_in_executor`

        Impact
        ------
        - side-effect réseau (AWS SQS).
        """
        trace_header = current_trace_header()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            self._executor, lambda: self._send(fare_event, trace_header)
        )
        logger.info("Published FareEvent: %s", fare_event.get("id"))
