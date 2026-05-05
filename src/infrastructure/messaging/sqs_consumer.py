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

Robustesse
----------
- Suppression UNIQUEMENT sur succès ou erreur définitive (sender absent,
  payload incompréhensible). Sur erreur transitoire (OpenAI, réseau,
  publish), on laisse SQS redélivrer après `visibility_timeout`.
- Heartbeat: prolongation périodique du visibility timeout pendant un
  parse long pour éviter la double livraison.
- Graceful shutdown: attend les messages en cours avant de fermer.
- `delete_message_batch` pour réduire les appels API.
"""

from __future__ import annotations

import asyncio
import base64
import json
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config

from application.services.ingestion_service import IngestionService
from config import settings
from domain.entities.email_message import EmailMessage
from domain.value_objects.email_metadata import EmailThreadMetadata
from logger import logger
from presentation.api.metrics import (
    consumer_inflight,
    consumer_messages_total,
)
from shared.exceptions import MissingSenderError
from xray_config import begin_segment, end_segment, subsegment

_BOTO_CONFIG = Config(
    connect_timeout=5,
    read_timeout=30,
    retries={"max_attempts": 3, "mode": "standard"},
)


def _safe_int(value, *, default: int = 0) -> int:
    """Convertit `value` en `int` sans lever d'exception."""
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


class SQSConsumer:
    """
    Consumer SQS du microservice.

    Garanties
    ---------
    - Un segment X-Ray top-level est créé par message.
    - Suppression: succès, sender absent, body vide ou max_retries dépassé.
    - Pas de suppression sur erreur transitoire → redelivery + DLQ AWS.
    - `change_message_visibility` périodique pendant traitement long.
    - `stop()` attend les messages en cours (timeout 30s).
    """

    def __init__(
        self,
        *,
        ingestion_service: IngestionService,
    ):
        if settings.aws_profile:
            session = boto3.Session(profile_name=settings.aws_profile)
            self._sqs = session.client(
                "sqs", region_name=settings.aws_region, config=_BOTO_CONFIG
            )
            logger.debug("SQSConsumer: profil AWS=%s", settings.aws_profile)
        else:
            self._sqs = boto3.client(
                "sqs", region_name=settings.aws_region, config=_BOTO_CONFIG
            )
            logger.debug("SQSConsumer: credentials AWS par défaut")

        self._ingestion_service = ingestion_service
        self._executor = ThreadPoolExecutor(
            max_workers=max(2, settings.sqs_max_workers)
        )
        self._running = False
        self._task: asyncio.Task | None = None
        self._inflight: set[asyncio.Task] = set()
        self._semaphore = asyncio.Semaphore(
            max(1, settings.sqs_max_concurrent_messages)
        )

        # Batcher de delete: les receipt handles sont accumulés ici puis
        # `delete_message_batch` envoie jusqu'à 10 deletes par appel.
        self._delete_queue: asyncio.Queue[str] = asyncio.Queue()
        self._delete_task: asyncio.Task | None = None
        self._delete_batch_size = 10
        self._delete_flush_interval = 0.5  # secondes

    # ─────────────────────────────────────────
    # CYCLE DE VIE
    # ─────────────────────────────────────────
    async def start(self) -> None:
        """Démarre la boucle de polling (tâche asyncio)."""
        if self._running:
            logger.warning("SQS Consumer déjà démarré")
            return
        if not settings.sqs_email_queue_url:
            logger.error("SQS_EMAIL_QUEUE_URL non configuré, consumer non démarré")
            return
        self._running = True
        logger.info(
            "Starting SQS Consumer for queue: %s", settings.sqs_email_queue_url
        )
        logger.info(
            "Consumer settings - max_messages: %s, wait_time: %ss, "
            "visibility_timeout: %ss, max_concurrent: %s",
            settings.sqs_max_messages,
            settings.sqs_wait_time_seconds,
            settings.sqs_visibility_timeout,
            settings.sqs_max_concurrent_messages,
        )
        self._task = asyncio.create_task(self._consume_loop())
        self._delete_task = asyncio.create_task(self._delete_batcher_loop())

    async def stop(self) -> None:
        """
        Arrête le consumer en attendant les messages en cours.

        Politique
        ---------
        - On signale au loop de s'arrêter (`_running = False`)
        - On attend les tâches in-flight (max 30s) → drain naturel
        - On annule la boucle de polling
        - On ferme la pool de threads (avec attente)
        """
        logger.info(
            "Stopping SQS Consumer (in-flight: %d)", len(self._inflight)
        )
        self._running = False

        if self._inflight:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._inflight, return_exceptions=True),
                    timeout=30.0,
                )
                logger.info("All in-flight messages drained")
            except asyncio.TimeoutError:
                logger.warning(
                    "Drain timeout reached, %d messages still in-flight",
                    len(self._inflight),
                )

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                logger.info("SQS Consumer task cancelled")

        # Flush du batcher de delete avant arrêt définitif
        if self._delete_task and not self._delete_task.done():
            await self._flush_delete_queue()
            self._delete_task.cancel()
            try:
                await self._delete_task
            except asyncio.CancelledError:
                pass

        self._executor.shutdown(wait=True)
        logger.info("SQS Consumer stopped")

    def is_healthy(self) -> bool:
        """Indique si le consumer est dans un état sain (pour /health/ready)."""
        if not settings.consumer_enabled:
            return True
        return (
            self._running
            and self._task is not None
            and not self._task.done()
        )

    # ─────────────────────────────────────────
    # BOUCLE PRINCIPALE
    # ─────────────────────────────────────────
    async def _consume_loop(self) -> None:
        """Boucle: receive → dispatch → drain en cas d'arrêt."""
        logger.info("SQS Consumer loop started - waiting for messages...")
        while self._running:
            try:
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(
                    self._executor,
                    lambda: self._sqs.receive_message(
                        QueueUrl=settings.sqs_email_queue_url,
                        MaxNumberOfMessages=settings.sqs_max_messages,
                        WaitTimeSeconds=settings.sqs_wait_time_seconds,
                        VisibilityTimeout=settings.sqs_visibility_timeout,
                        AttributeNames=["ApproximateReceiveCount"],
                    ),
                )

                messages = response.get("Messages", [])
                if not messages:
                    continue

                logger.info(
                    "Received %d messages from SQS",
                    len(messages),
                )
                for m in messages:
                    task = asyncio.create_task(self._process_message(m))
                    self._inflight.add(task)
                    task.add_done_callback(self._inflight.discard)

            except asyncio.CancelledError:
                logger.info("Consume loop cancelled")
                raise
            except Exception as e:
                logger.error("Error consuming messages: %s", e, exc_info=True)
                await asyncio.sleep(settings.consumer_error_delay_seconds)

    async def _process_message(self, message: dict) -> None:
        """Wrapper de contrôle de concurrence (sémaphore)."""
        async with self._semaphore:
            consumer_inflight.inc()
            try:
                await self._process_message_inner(message)
            finally:
                consumer_inflight.dec()

    # ─────────────────────────────────────────
    # TRAITEMENT D'UN MESSAGE
    # ─────────────────────────────────────────
    async def _process_message_inner(self, message: dict) -> None:
        """
        Traite un message SQS unique.

        Politique de delete
        -------------------
        - succès → delete
        - sender absent ou body vide → delete (erreur définitive)
        - erreur transitoire & receive_count < max_retries → pas de delete
        - erreur transitoire & receive_count >= max_retries → delete (DLQ)
        """
        begin_segment("ingestion_sqs_process_message")
        message_id = message.get("MessageId")
        receipt_handle = message.get("ReceiptHandle")
        attributes = message.get("Attributes", {}) or {}
        receive_count = _safe_int(
            attributes.get("ApproximateReceiveCount"), default=1
        )
        max_retries = max(1, settings.consumer_max_retries)
        should_delete = False

        try:
            body = message.get("Body")
            logger.info(
                "Processing message: sqs_message_id=%s receive_count=%d",
                message_id,
                receive_count,
            )

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
                    "Message %s missing email_body, deleting from queue",
                    message_id,
                )
                consumer_messages_total.labels(outcome="empty_body").inc()
                should_delete = True
                return

            email = EmailMessage(
                sender=sender or "",
                subject=subject,
                body_text=email_body,
                thread=thread,
            )

            heartbeat_task: asyncio.Task | None = None
            if receipt_handle:
                heartbeat_task = asyncio.create_task(
                    self._heartbeat(receipt_handle, message_id)
                )

            try:
                with subsegment("ingestion_process_email"):
                    logger.info(
                        "Starting process: sqs_message_id=%s sender_len=%d "
                        "subject_len=%d body_len=%d",
                        message_id,
                        len(email.sender or ""),
                        len(email.subject or ""),
                        len(email.body_text or ""),
                    )
                    fare_event = await (
                        self._ingestion_service.process_email_use_case.execute(email)
                    )

                logger.info(
                    "Successfully processed message: sqs_message_id=%s "
                    "fare_event_id=%s",
                    message_id,
                    fare_event.get("id") if isinstance(fare_event, dict) else None,
                )
                consumer_messages_total.labels(outcome="success").inc()
                should_delete = True
            except MissingSenderError as e:
                logger.error(
                    "Cannot process message (no sender): sqs_message_id=%s err=%s",
                    message_id,
                    e,
                )
                consumer_messages_total.labels(outcome="missing_sender").inc()
                should_delete = True
            except Exception as e:
                if receive_count >= max_retries:
                    logger.error(
                        "Poison message after %d retries, deleting "
                        "(DLQ should take over): sqs_message_id=%s err=%s",
                        receive_count,
                        message_id,
                        e,
                        exc_info=True,
                    )
                    consumer_messages_total.labels(outcome="poison").inc()
                    should_delete = True
                else:
                    logger.warning(
                        "Transient error, message will be redelivered: "
                        "sqs_message_id=%s receive_count=%d err=%s",
                        message_id,
                        receive_count,
                        e,
                        exc_info=True,
                    )
                    consumer_messages_total.labels(outcome="transient_error").inc()
                    should_delete = False
            finally:
                if heartbeat_task is not None:
                    heartbeat_task.cancel()
                    try:
                        await heartbeat_task
                    except asyncio.CancelledError:
                        pass
                    except Exception as hb_err:
                        logger.debug(
                            "Heartbeat task ended with error: %s", hb_err
                        )

        except Exception as e:
            logger.error(
                "Unrecoverable error processing message %s: %s",
                message_id,
                e,
                exc_info=True,
            )
            should_delete = True
        finally:
            if should_delete and receipt_handle:
                try:
                    await self._delete_async(receipt_handle)
                except Exception as delete_err:
                    logger.error(
                        "Failed to delete message %s: %s",
                        message_id,
                        delete_err,
                        exc_info=True,
                    )
            end_segment()

    # ─────────────────────────────────────────
    # HEARTBEAT (extension du visibility timeout)
    # ─────────────────────────────────────────
    async def _heartbeat(self, receipt_handle: str, message_id: str | None) -> None:
        """
        Prolonge périodiquement le visibility timeout pendant le traitement.

        Permet de gérer des parses long (OpenAI lent) sans risque de
        redelivery par SQS au milieu du traitement.
        """
        interval = max(10, settings.sqs_heartbeat_interval_seconds)
        extend = max(60, settings.sqs_heartbeat_extend_seconds)
        # `asyncio.CancelledError` se propage naturellement (pas d'except).
        while True:
            await asyncio.sleep(interval)
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    self._executor,
                    lambda: self._sqs.change_message_visibility(
                        QueueUrl=settings.sqs_email_queue_url,
                        ReceiptHandle=receipt_handle,
                        VisibilityTimeout=extend,
                    ),
                )
                logger.debug(
                    "Extended visibility for message %s by %ds",
                    message_id,
                    extend,
                )
            except Exception as e:
                logger.warning(
                    "Failed to extend visibility for %s: %s", message_id, e
                )
                return

    # ─────────────────────────────────────────
    # PARSING DU BODY (SNS / SES)
    # ─────────────────────────────────────────
    def _parse_message_body(self, body: str | None) -> dict:
        """
        Parse le `Body` d'un message SQS.

        Entrées attendues
        -----------------
        - JSON direct (format libre)
        - JSON SNS wrapper contenant un champ `Message` (string JSON)
        - Message SES (inner JSON avec `mail` et `content` base64)
        """
        if not body:
            return {}
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            logger.error(
                "Failed to parse message body as JSON: %s... Error: %s",
                body[:200],
                e,
            )
            return {}

        if "Message" in data:
            try:
                inner_message = json.loads(data.get("Message", "{}"))
            except json.JSONDecodeError:
                inner_message = {}

            if "mail" in inner_message:
                return self._unwrap_ses(inner_message)

            return inner_message

        return data

    @staticmethod
    def _unwrap_ses(inner_message: dict) -> dict:
        """Unwrap d'une notification SES (inner d'un message SNS)."""
        mail = inner_message.get("mail", {}) or {}
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

    # ─────────────────────────────────────────
    # DELETE (batcher)
    # ─────────────────────────────────────────
    async def _delete_async(self, receipt_handle: str) -> None:
        """Pousse un receipt dans la queue du batcher (non bloquant)."""
        await self._delete_queue.put(receipt_handle)

    async def _delete_batcher_loop(self) -> None:
        """
        Boucle du batcher: agrège jusqu'à `_delete_batch_size` receipts ou
        flush après `_delete_flush_interval` secondes, puis envoie un
        `delete_message_batch` SQS (jusqu'à 10 receipts par appel).

        Note
        ----
        `asyncio.CancelledError` se propage naturellement (pas d'except).
        """
        while True:
            first = await self._delete_queue.get()
            batch: list[str] = [first]

            # Agrège jusqu'à batch_size, avec un timeout court
            while len(batch) < self._delete_batch_size:
                try:
                    nxt = await asyncio.wait_for(
                        self._delete_queue.get(),
                        timeout=self._delete_flush_interval,
                    )
                    batch.append(nxt)
                except asyncio.TimeoutError:
                    break

            await self._delete_batch(batch)

    async def _flush_delete_queue(self) -> None:
        """Vide la queue de delete avant l'arrêt (best-effort)."""
        pending: list[str] = []
        while not self._delete_queue.empty():
            pending.append(self._delete_queue.get_nowait())
        for i in range(0, len(pending), self._delete_batch_size):
            await self._delete_batch(pending[i : i + self._delete_batch_size])

    async def _delete_batch(self, receipts: list[str]) -> None:
        """Effectue un `delete_message_batch` (jusqu'à 10 receipts)."""
        if not receipts:
            return

        entries = [
            {"Id": str(idx), "ReceiptHandle": rh}
            for idx, rh in enumerate(receipts)
        ]
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                self._executor,
                lambda: self._sqs.delete_message_batch(
                    QueueUrl=settings.sqs_email_queue_url,
                    Entries=entries,
                ),
            )
            failed = response.get("Failed", []) if isinstance(response, dict) else []
            if failed:
                logger.warning(
                    "delete_message_batch partial failure: %d/%d failed: %s",
                    len(failed),
                    len(entries),
                    failed,
                )
        except Exception as e:
            logger.error(
                "delete_message_batch failed (size=%d): %s",
                len(entries),
                e,
                exc_info=True,
            )
