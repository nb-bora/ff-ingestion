"""
Extracteurs Infrastructure : exception → `ErrorInfo`, message SQS → `SourceArtifact`.

Rôle
----
Isoler les détails techniques (Python `traceback`, format des messages SQS/SNS,
décodage SES) qui n'ont pas leur place dans Domain ni Application.

Ces fonctions sont **pures** au sens fonctionnel (pas d'I/O, pas de side
effect) et purement utilitaires : elles transforment un input en VO Domain.
"""

from __future__ import annotations

import json
import traceback

from domain.value_objects.error_info import (
    MESSAGE_MAX_CHARS,
    STACK_MAX_CHARS,
    ErrorInfo,
)
from domain.value_objects.source_artifact import (
    RAW_BODY_MAX_CHARS,
    SourceArtifact,
)

_RECEIPT_HANDLE_VISIBLE_CHARS = 8


def extract_error_info(exc: BaseException) -> ErrorInfo:
    """
    Construit un `ErrorInfo` à partir d'une exception Python.

    Limites
    -------
    - `message` tronqué à `MESSAGE_MAX_CHARS` (1000)
    - `stack` tronqué à `STACK_MAX_CHARS` (4000)
    """
    tb_list = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    last = tb_list[-1] if tb_list else None
    stack_str = "".join(traceback.format_exception(exc))
    return ErrorInfo(
        class_=type(exc).__name__,
        message=(str(exc) or "")[:MESSAGE_MAX_CHARS],
        module=exc.__class__.__module__,
        file=last.filename if last is not None else None,
        line=last.lineno if last is not None else None,
        function=last.name if last is not None else None,
        stack=stack_str[:STACK_MAX_CHARS] if stack_str else None,
    )


def _redact_receipt_handle(handle: str | None) -> str | None:
    """Tronque un receipt handle (sécurité : ne jamais loguer en clair)."""
    if not handle:
        return None
    visible = handle[:_RECEIPT_HANDLE_VISIBLE_CHARS]
    return f"{visible}...redacted"


def _ses_subject_from_inner(inner: dict) -> str | None:
    """Extrait `subject` d'une notification SES inner-message."""
    mail = inner.get("mail") or {}
    common = mail.get("commonHeaders") or {}
    return common.get("subject")


def _ses_sender_from_inner(inner: dict) -> str | None:
    """Extrait l'expéditeur d'une notification SES inner-message."""
    mail = inner.get("mail") or {}
    return mail.get("source")


def _ses_message_id_from_inner(inner: dict) -> str | None:
    """Extrait le SES `messageId` d'une notification SES inner-message."""
    mail = inner.get("mail") or {}
    return mail.get("messageId")


def extract_source_artifact(
    sqs_message: dict | None,
    *,
    queue_url: str | None = None,
    fallback_sender: str | None = None,
    fallback_subject: str | None = None,
) -> SourceArtifact:
    """
    Construit un `SourceArtifact` à partir d'un message SQS brut.

    Stratégie
    ---------
    - `MessageId` et `ReceiptHandle` viennent du SQS message
    - On essaie de décoder le `Body` comme JSON SNS+SES (best-effort) pour
      récupérer `sender`, `subject`, `ses_message_id`
    - `raw_body_excerpt` tronqué à `RAW_BODY_MAX_CHARS`
    - `size_bytes` = taille du dict sérialisé en JSON
    """
    msg = sqs_message or {}
    body_raw = msg.get("Body") or ""
    excerpt = body_raw[:RAW_BODY_MAX_CHARS] if body_raw else None

    sender: str | None = fallback_sender
    subject: str | None = fallback_subject
    ses_id: str | None = None

    if body_raw:
        try:
            data = json.loads(body_raw)
            inner_msg = data.get("Message")
            if isinstance(inner_msg, str):
                try:
                    inner = json.loads(inner_msg)
                    if isinstance(inner, dict) and "mail" in inner:
                        sender = sender or _ses_sender_from_inner(inner)
                        subject = subject or _ses_subject_from_inner(inner)
                        ses_id = _ses_message_id_from_inner(inner)
                except json.JSONDecodeError:
                    pass
            elif isinstance(data, dict) and "mail" in data:
                sender = sender or _ses_sender_from_inner(data)
                subject = subject or _ses_subject_from_inner(data)
                ses_id = _ses_message_id_from_inner(data)
        except json.JSONDecodeError:
            pass

    try:
        size_bytes = len(json.dumps(msg, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        size_bytes = None

    return SourceArtifact(
        kind="sqs_message",
        queue_url=queue_url,
        sqs_message_id=msg.get("MessageId"),
        receipt_handle_redacted=_redact_receipt_handle(msg.get("ReceiptHandle")),
        raw_body_excerpt=excerpt,
        ses_message_id=ses_id,
        sender=sender,
        subject=subject,
        size_bytes=size_bytes,
    )
