"""
Entité Domain `NotificationEvent`.

Rôle
----
Représenter un événement de notification publié sur la queue
`fairfare-box-notifications` à destination du microservice `ff-notifier` qui
transforme l'événement en email (utilisateur ou support).

Forme
-----
Une enveloppe commune (`schema_version`, `event_id`, `category`, ...) + un bloc
`variables` typé selon `category` :
- `user_untreatable` : `missing_fields[]`, `original_email`, `human_summary`,
  `next_action`, ...
- `support_alert` : `error`, `occurrence`, `source_artifact`, `runbook_url`, ...

Conventions
-----------
- `event_id` est déterministe (uuid5) → idempotence côté notifier.
- Limite SQS 256 KB : on tronque agressivement (`stack` 4 KB, `raw_body` 1 KB,
  snippet 200 chars). Les constantes vivent ici (entité = source de vérité).
- L'entité ne sait rien d'AWS/SQS : la sérialisation finale (JSON, attributs)
  est faite par `infrastructure.messaging.sqs_notification_publisher`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from domain.enums.failure_code import FailureCode
from domain.enums.notification import (
    NextAction,
    NotificationCategory,
    NotificationSeverity,
)
from domain.value_objects.error_info import ErrorInfo
from domain.value_objects.missing_field import MissingField
from domain.value_objects.source_artifact import SourceArtifact

SCHEMA_VERSION = 1
SNIPPET_MAX_CHARS = 200

_NOTIFICATION_NAMESPACE = uuid.UUID("c1d4f9a2-3e7b-4a1c-9e2d-7b5a6f8c1234")


def _build_event_id(
    *, source_message_id: str | None, failure_code: FailureCode
) -> str:
    """
    Calcule un `event_id` déterministe (uuid5).

    - Si `source_message_id` connu (Message-ID RFC822 ou MessageId SQS),
      l'event est idempotent : republier le même couple (source, code) ne
      générera jamais un second email côté notifier.
    - Sinon (cas dégradé), fallback `uuid4` pour ne pas bloquer la notif.
    """
    if source_message_id:
        seed = f"{source_message_id}|{failure_code.value}"
        return str(uuid.uuid5(_NOTIFICATION_NAMESPACE, seed))
    return str(uuid.uuid4())


def _truncate(value: str | None, limit: int) -> str | None:
    """Tronque une chaîne à `limit` caractères (None passe-plat)."""
    if value is None:
        return None
    return value if len(value) <= limit else value[:limit]


@dataclass(frozen=True)
class NotificationRecipient:
    """Destinataire fonctionnel (le notifier mappe sur un email réel)."""

    type: str
    email: str | None = None
    locale: str = "fr"

    def to_dict(self) -> dict:
        return {"type": self.type, "email": self.email, "locale": self.locale}


@dataclass(frozen=True)
class NotificationContext:
    """Contexte de l'événement (corrélation, threading, traçage)."""

    sender: str | None = None
    subject: str | None = None
    source_message_id: str | None = None
    received_at: str | None = None
    trace_id: str | None = None
    correlation_id: str | None = None
    sqs_source_message_id: str | None = None
    receive_count: int | None = None

    def to_dict(self) -> dict:
        return {
            "sender": self.sender,
            "subject": self.subject,
            "source_message_id": self.source_message_id,
            "received_at": self.received_at,
            "trace_id": self.trace_id,
            "correlation_id": self.correlation_id,
            "sqs_source_message_id": self.sqs_source_message_id,
            "receive_count": self.receive_count,
        }


@dataclass(frozen=True)
class NotificationEvent:
    """Événement publié sur la queue notifications."""

    event_id: str
    occurred_at: str
    service: str
    environment: str
    category: NotificationCategory
    severity: NotificationSeverity
    template_id: str
    failure_code: FailureCode
    recipient: NotificationRecipient
    context: NotificationContext
    variables: dict
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        """Sérialise l'événement en dict JSON-serializable."""
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "occurred_at": self.occurred_at,
            "service": self.service,
            "environment": self.environment,
            "category": self.category.value,
            "severity": self.severity.value,
            "template_id": self.template_id,
            "failure_code": self.failure_code.value,
            "recipient": self.recipient.to_dict(),
            "context": self.context.to_dict(),
            "variables": self.variables,
        }

    @staticmethod
    def make_user_untreatable(
        *,
        service: str,
        environment: str,
        failure_code: FailureCode,
        template_id: str,
        sender: str,
        locale: str = "fr",
        missing_fields: list[MissingField] | None = None,
        blocking_rules: list[str] | None = None,
        non_blocking_rules: list[str] | None = None,
        signals: list[str] | None = None,
        original_subject: str | None = None,
        original_received_at: str | None = None,
        original_snippet: str | None = None,
        human_summary: str | None = None,
        next_action: NextAction = NextAction.reply_with_missing_info,
        support_contact: str | None = None,
        source_message_id: str | None = None,
        trace_id: str | None = None,
        correlation_id: str | None = None,
        sqs_source_message_id: str | None = None,
        receive_count: int | None = None,
        severity: NotificationSeverity = NotificationSeverity.warning,
    ) -> NotificationEvent:
        """Construit un événement `user_untreatable` complet."""
        recipient = NotificationRecipient(
            type="user", email=sender, locale=locale
        )
        ctx = NotificationContext(
            sender=sender,
            subject=original_subject,
            source_message_id=source_message_id,
            received_at=original_received_at,
            trace_id=trace_id,
            correlation_id=correlation_id,
            sqs_source_message_id=sqs_source_message_id,
            receive_count=receive_count,
        )
        variables = {
            "user_first_name": None,
            "original_email": {
                "subject": original_subject,
                "received_at": original_received_at,
                "snippet": _truncate(original_snippet, SNIPPET_MAX_CHARS),
            },
            "missing_fields": [mf.to_dict() for mf in (missing_fields or [])],
            "blocking_rules": list(blocking_rules or []),
            "non_blocking_rules": list(non_blocking_rules or []),
            "signals": list(signals or []),
            "human_summary": human_summary,
            "next_action": next_action.value,
            "support_contact": support_contact,
        }
        return NotificationEvent(
            event_id=_build_event_id(
                source_message_id=source_message_id or sqs_source_message_id,
                failure_code=failure_code,
            ),
            occurred_at=datetime.now(UTC).isoformat(),
            service=service,
            environment=environment,
            category=NotificationCategory.user_untreatable,
            severity=severity,
            template_id=template_id,
            failure_code=failure_code,
            recipient=recipient,
            context=ctx,
            variables=variables,
        )

    @staticmethod
    def make_support_alert(
        *,
        service: str,
        environment: str,
        failure_code: FailureCode,
        template_id: str,
        error: ErrorInfo,
        source_artifact: SourceArtifact | None = None,
        sender: str | None = None,
        subject: str | None = None,
        source_message_id: str | None = None,
        trace_id: str | None = None,
        correlation_id: str | None = None,
        sqs_source_message_id: str | None = None,
        receive_count: int | None = None,
        host: str | None = None,
        deploy_sha: str | None = None,
        first_seen_at: str | None = None,
        runbook_url: str | None = None,
        human_summary: str | None = None,
        severity: NotificationSeverity = NotificationSeverity.error,
    ) -> NotificationEvent:
        """Construit un événement `support_alert` complet."""
        recipient = NotificationRecipient(type="support", email=None, locale="en")
        ctx = NotificationContext(
            sender=sender,
            subject=subject,
            source_message_id=source_message_id,
            received_at=first_seen_at,
            trace_id=trace_id,
            correlation_id=correlation_id,
            sqs_source_message_id=sqs_source_message_id,
            receive_count=receive_count,
        )
        variables = {
            "error": error.to_dict(),
            "occurrence": {
                "trace_id": trace_id,
                "host": host,
                "deploy_sha": deploy_sha,
                "receive_count": receive_count,
                "first_seen_at": first_seen_at,
            },
            "source_artifact": (
                source_artifact.to_dict() if source_artifact is not None else None
            ),
            "runbook_url": runbook_url,
            "human_summary": human_summary,
        }
        return NotificationEvent(
            event_id=_build_event_id(
                source_message_id=source_message_id or sqs_source_message_id,
                failure_code=failure_code,
            ),
            occurred_at=datetime.now(UTC).isoformat(),
            service=service,
            environment=environment,
            category=NotificationCategory.support_alert,
            severity=severity,
            template_id=template_id,
            failure_code=failure_code,
            recipient=recipient,
            context=ctx,
            variables=variables,
        )
