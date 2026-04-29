"""
Consumer SQS: ingère des messages email (SES→SNS→SQS), parse, publie, puis delete.

Rôle
----
- Poll une queue SQS source (`settings.sqs_email_queue_url`)
- Dépaqueter:
  - wrapper SNS (`data["Message"]`)
  - notification SES (`inner["mail"]` et `inner["content"]` base64)
- Construire un `domain.entities.EmailMessage` + threading metadata
- Appeler `application.use_cases.ProcessEmailUseCase` (parse + publish)
- Supprimer le message source (SQS delete) après traitement

Utilisé par
---------
- `main.lifespan` via `presentation.api.dependencies.get_sqs_consumer`

Utilise
-------
- `boto3` (SQS client)
- `ThreadPoolExecutor` pour exécuter `receive_message` et `delete_message` (boto3 synchrone)
- `xray_config.begin_segment/subsegment/end_segment` (observabilité)
- `IngestionService.process_email_use_case` (orchestration Application)

Impact / effets de bord
----------------------
- Trafic réseau AWS (SQS receive/delete)
- Publication downstream (via `ProcessEmailUseCase` + `SQSPublisher`)
- Concurrence: traite plusieurs messages d’un poll en parallèle, bornée par
  `settings.sqs_max_concurrent_messages` (sémaphore).
"""

from __future__ import annotations

import asyncio
import base64
import json
from concurrent.futures import ThreadPoolExecutor

import boto3
from pydantic import ValidationError

from application.interfaces.message_publisher import IMessagePublisher
from application.services.ingestion_service import IngestionService
from config import settings
from domain.entities.email_message import EmailMessage
from domain.value_objects.email_metadata import EmailThreadMetadata
from logger import logger
from presentation.schemas.fare_event_schema import FareEventSchema
from shared.exceptions import MissingSenderError
from xray_config import begin_segment, end_segment, subsegment


class SQSConsumer:
    """
    Consumer SQS du microservice.

    Invariants (parité `ff-ingestion`)
    ---------------------------------
    - Un segment X-Ray top-level est créé par message (`ingestion_sqs_process_message`)
    - Les messages sans `email_body` sont supprimés (pas de retry "utile")
    - Les messages sans sender déclenchent un log d’erreur puis suppression
    - Le delete est tenté en `finally` lorsque possible
    """

    def __init__(
        self,
        *,
        ingestion_service: IngestionService,
        publisher: IMessagePublisher,
    ):
        """
        Initialise le consumer.

        Utilise
        -------
        - `settings.aws_profile` / `settings.aws_region` pour le client boto3
        - `settings.sqs_max_workers` pour la pool de threads
        - `settings.sqs_max_concurrent_messages` pour borner la concurrence async

        Impact
        ------
        - Crée une pool de threads (à arrêter via `stop()`).
        """
        if settings.aws_profile:
            session = boto3.Session(profile_name=settings.aws_profile)
            self._sqs = session.client("sqs", region_name=settings.aws_region)
            logger.debug("SQSConsumer: profil AWS=%s", settings.aws_profile)
        else:
            self._sqs = boto3.client("sqs", region_name=settings.aws_region)
            logger.debug("SQSConsumer: credentials AWS par défaut")

        self._ingestion_service = ingestion_service
        self._publisher = publisher
        self._executor = ThreadPoolExecutor(
            max_workers=max(2, settings.sqs_max_workers)
        )
        self._running = False
        self._task: asyncio.Task | None = None
        self._semaphore = asyncio.Semaphore(
            max(1, int(getattr(settings, "sqs_max_concurrent_messages", 10)))
        )

    async def start(self) -> None:
        """
        Démarre la boucle de polling (tâche asyncio).

        Utilisé par
        ---------
        - `main.lifespan`
        """
        if self._running:
            logger.warning("SQS Consumer déjà démarré")
            return
        self._running = True
        logger.info("Starting SQS Consumer for queue: %s", settings.sqs_email_queue_url)
        logger.info(
            "Consumer settings - max_messages: %s, wait_time: %ss, visibility_timeout: %ss",
            settings.sqs_max_messages,
            settings.sqs_wait_time_seconds,
            settings.sqs_visibility_timeout,
        )
        self._task = asyncio.create_task(self._consume_loop())

    async def stop(self) -> None:
        """
        Arrête la boucle de polling et ferme la pool de threads.

        Utilisé par
        ---------
        - `main.lifespan` (shutdown)
        """
        logger.info("Stopping SQS Consumer")
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                logger.info("SQS Consumer task cancelled")
        self._executor.shutdown(wait=False)

    async def _consume_loop(self) -> None:
        """
        Boucle principale: `receive_message` puis dispatch des messages.

        Note
        ----
        `receive_message` est exécuté dans un thread car boto3 est bloquant.
        """
        logger.info("SQS Consumer loop started - waiting for messages...")
        poll_count = 0
        while self._running:
            try:
                poll_count += 1
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    self._executor,
                    lambda: self._sqs.receive_message(
                        QueueUrl=settings.sqs_email_queue_url,
                        MaxNumberOfMessages=settings.sqs_max_messages,
                        WaitTimeSeconds=settings.sqs_wait_time_seconds,
                        VisibilityTimeout=settings.sqs_visibility_timeout,
                    ),
                )

                messages = response.get("Messages", [])
                if not messages:
                    continue

                message_ids = [
                    m.get("MessageId") for m in messages if isinstance(m, dict)
                ]
                logger.info(
                    "Received %d messages from SQS: message_ids=%s",
                    len(messages),
                    message_ids,
                )
                await asyncio.gather(*(self._process_message(m) for m in messages))

            except Exception as e:
                logger.error("Error consuming messages: %s", e, exc_info=True)
                await asyncio.sleep(settings.consumer_error_delay_seconds)

    async def _process_message(self, message: dict) -> None:
        """
        Wrapper de contrôle de concurrence (sémaphore).

        Utilisé par
        ---------
        - `_consume_loop` via `asyncio.gather`
        """
        async with self._semaphore:
            await self._process_message_inner(message)

    async def _process_message_inner(self, message: dict) -> None:
        """
        Traite un message SQS unique.

        Étapes
        ------
        - segment X-Ray (top-level)
        - parse JSON + unwrap SNS/SES
        - création `EmailMessage`
        - appel use-case (parse+publish)
        - delete SQS (quoi qu’il arrive, si receipt_handle présent)

        Erreurs
        ------
        - Les exceptions sont loggées; le consumer continue à poll.
        """
        begin_segment("ingestion_sqs_process_message")
        message_id = message.get("MessageId")
        receipt_handle = message.get("ReceiptHandle")
        try:
            body = message.get("Body")
            logger.info("Processing message: %s", message_id)

            msg_data = self._parse_message_body(body)
            email_body = msg_data.get("email_body") or ""
            sender = msg_data.get("sender")
            subject = msg_data.get("subject")

            thread = EmailThreadMetadata(
                message_id=msg_data.get("message_id"),
                in_reply_to=msg_data.get("in_reply_to"),
                references=msg_data.get("references"),
                reply_to=msg_data.get("reply_to"),
            )

            if not email_body:
                logger.warning(
                    "Message %s missing email_body, deleting from queue", message_id
                )
                if receipt_handle:
                    await self._delete_async(receipt_handle)
                return

            email = EmailMessage(
                sender=sender or "",
                subject=subject,
                body_text=email_body,
                thread=thread,
            )

            try:
                with subsegment("ingestion_parse_email"):
                    logger.info(
                        "Starting parse: sqs_message_id=%s sender=%s subject=%s body_len=%d",
                        message_id,
                        email.sender,
                        (email.subject or "")[:120],
                        len(email.body_text or ""),
                    )
                    fare_event = (
                        await self._ingestion_service.process_email_use_case.execute(
                            email
                        )
                    )

                # Point d’extension: ajustements/contraintes post-parse (avant publish).
                fare_event = self._post_parse_adjustments(fare_event)

                # Log complet du résultat de parse (après post-parse hook).
                #logger.info(
                #    "Parse result (FareEvent): sqs_message_id=%s fare_event=%s",
                #    message_id,
                #    json.dumps(fare_event, ensure_ascii=False),
                #)

                with subsegment("ingestion_publish_fare_event"):
                    await self._publisher.publish_fare_event(fare_event)

                logger.info("Successfully processed message: %s", message_id)
            except MissingSenderError as e:
                logger.error("Cannot process message (no sender): %s", e)
                logger.info("Deleting message %s (cannot respond)", message_id)
            finally:
                if receipt_handle:
                    await self._delete_async(receipt_handle)

        except Exception as e:
            logger.error(
                "Error processing message %s: %s", message_id, e, exc_info=True
            )
        finally:
            end_segment()

    def _parse_message_body(self, body: str | None) -> dict:
        """
        Parse le `Body` d’un message SQS.

        Entrées attendues
        -----------------
        - JSON direct (format libre)
        - JSON SNS wrapper contenant un champ `Message` (string JSON)
        - Message SES (inner JSON avec `mail` et `content` base64)

        Sortie
        ------
        Un dict normalisé au minimum sur:
        - `email_body`, `sender`, `subject`
        - `message_id`, `in_reply_to`, `references`, `reply_to`

        Utilisé par
        ---------
        - `_process_message_inner`
        """
        if not body:
            return {}
        try:
            data = json.loads(body)

            # SNS wrapper
            if "Message" in data:
                inner_message_str = data.get("Message", "{}")
                inner_message = json.loads(inner_message_str)

                # SES notification format
                if "mail" in inner_message:
                    mail = inner_message.get("mail", {})
                    sender = mail.get("source")
                    message_id = mail.get("messageId")

                    common_headers = mail.get("commonHeaders", {}) or {}
                    subject = common_headers.get("subject", "")
                    common_message_id = common_headers.get("messageId")
                    in_reply_to = common_headers.get("inReplyTo")
                    references = common_headers.get("references")
                    reply_to = common_headers.get("replyTo")

                    header_map = {
                        (h.get("name") or "").lower(): h.get("value")
                        for h in mail.get("headers", [])
                        if isinstance(h, dict)
                    }
                    in_reply_to = in_reply_to or header_map.get("in-reply-to")
                    references = references or header_map.get("references")
                    reply_to = reply_to or header_map.get("reply-to")
                    message_id = (
                        message_id or common_message_id or header_map.get("message-id")
                    )

                    if isinstance(reply_to, list):
                        reply_to = reply_to[0] if reply_to else None
                    if isinstance(references, list):
                        references = " ".join(references)

                    content_b64 = inner_message.get("content", "")
                    email_body = ""
                    if content_b64:
                        try:
                            email_body = base64.b64decode(content_b64).decode(
                                "utf-8", errors="replace"
                            )
                        except Exception as e:
                            logger.warning("Failed to decode base64 content: %s", e)
                            email_body = content_b64

                    return {
                        "email_body": email_body,
                        "sender": sender,
                        "subject": subject,
                        "message_id": message_id,
                        "in_reply_to": in_reply_to,
                        "references": references,
                        "reply_to": reply_to,
                        "mail": mail,
                    }

                return inner_message

            return data
        except json.JSONDecodeError as e:
            logger.error(
                "Failed to parse message body as JSON: %s... Error: %s", body[:200], e
            )
            return {}

    def _delete(self, receipt_handle: str) -> None:
        """
        Delete synchrone boto3 (SQS).

        Utilisé par
        ---------
        - `_delete_async` via `run_in_executor`
        """
        self._sqs.delete_message(
            QueueUrl=settings.sqs_email_queue_url,
            ReceiptHandle=receipt_handle,
        )

    async def _delete_async(self, receipt_handle: str) -> None:
        """
        Delete async (wrapper threadpool).

        Utilisé par
        ---------
        - `_process_message_inner` (cleanup)
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, lambda: self._delete(receipt_handle))

    def _post_parse_adjustments(self, fare_event: dict) -> dict:
        """
        Hook post-parse (avant publication).

        Objectif
        --------
        Centraliser ici toute logique future du type:
        - validations supplémentaires
        - normalisations/contraintes
        - enrichissements (si nécessaire)

        Important
        ---------
        Pour l’instant, ce hook ne modifie rien (identité) afin de préserver
        strictement le comportement actuel.
        """
        try:
            # Validation "sans effet": on garantit la conformité du schéma,
            # mais on retourne le dict original pour éviter tout changement de payload.
            _ = FareEventSchema.model_validate(fare_event)
        except ValidationError as e:
            # Zéro impact workflow: on log, mais on n'empêche pas la publication.
            logger.error(
                "FareEventSchema validation failed (non-blocking): errors=%s fare_event=%s",
                e.errors(),
                json.dumps(fare_event, ensure_ascii=False),
            )
        return fare_event
