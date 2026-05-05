"""
Use-case Application: publication des notifications d'échec.

Rôle
----
Centraliser la fabrication d'un `NotificationEvent` (utilisateur ou support) à
partir d'un contexte d'erreur, et déléguer la publication à un
`INotificationPublisher`.

Garanties
---------
- **Ne lève jamais** : toute erreur de fabrication ou de publication est
  capturée, loguée et tracée via métriques. Le pipeline d'ingestion principal
  ne doit jamais tomber pour une notification ratée.
- **Throttle** anti-spam pour les `support_alert` : un cache mémoire local
  filtre les rafales du même `failure_code` (TTL configurable). Les
  `user_untreatable` ne sont **pas** throttlés (idempotence garantie via
  `event_id` uuid5).
- **Mapping `failure_code → template_id`** centralisé ici (table interne).

Pourquoi ici (Application) ?
----------------------------
- L'orchestration "construire l'event + publier + gérer throttle" est
  applicative.
- Aucune dépendance AWS : on appelle `INotificationPublisher` (port).
- Les VOs riches (`ErrorInfo`, `SourceArtifact`) viennent du Domain ; leur
  extraction depuis une exception/un message SQS est faite par
  `infrastructure.error_collection.extractors` puis injectée ici.

Utilisé par
---------
- `application.use_cases.ProcessEmailUseCase` (sur `ParseError`)
- `infrastructure.messaging.SQSConsumer` (sur missing_sender / poison / empty_body)
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from application.interfaces.notification_publisher import INotificationPublisher
from config import settings
from domain.entities.email_message import EmailMessage
from domain.entities.notification_event import NotificationEvent
from domain.enums.failure_code import FailureCode
from domain.enums.notification import (
    NextAction,
    NotificationSeverity,
)
from domain.rules.tier1_resolver import build_missing_fields
from domain.value_objects.error_info import ErrorInfo
from domain.value_objects.missing_field import MissingField
from domain.value_objects.source_artifact import SourceArtifact
from logger import logger
from xray_config import current_trace_header


# ─────────────────────────────────────────────────────────────────────────────
# MAPPING failure_code → template_id (source de vérité côté Ingestion)
# ─────────────────────────────────────────────────────────────────────────────
# Synchronisé avec docs/NOTIFICATIONS_CONTRACT.md.
_USER_TEMPLATE_BY_CODE: dict[FailureCode, str] = {
    FailureCode.PARSE_FAILED: "user.untreatable.parse_failed",
    FailureCode.POISON_MESSAGE: "user.untreatable.poison_message",
}
_SUPPORT_TEMPLATE_BY_CODE: dict[FailureCode, str] = {
    FailureCode.MISSING_SENDER: "support.missing_sender",
    FailureCode.EMPTY_BODY: "support.poison_message",
    FailureCode.POISON_MESSAGE: "support.poison_message",
    FailureCode.OPENAI_UNAVAILABLE: "support.server_error",
    FailureCode.UNKNOWN_INGESTION_ERROR: "support.server_error",
}
_DEFAULT_USER_TEMPLATE = "user.untreatable.tier1_hard"
_DEFAULT_SUPPORT_TEMPLATE = "support.server_error"


def _user_template_for(code: FailureCode) -> str:
    """Retourne le `template_id` user pour un code donné."""
    return _USER_TEMPLATE_BY_CODE.get(code, _DEFAULT_USER_TEMPLATE)


def _support_template_for(code: FailureCode) -> str:
    """Retourne le `template_id` support pour un code donné."""
    return _SUPPORT_TEMPLATE_BY_CODE.get(code, _DEFAULT_SUPPORT_TEMPLATE)


def _trace_id_from_header() -> str | None:
    """Extrait le `Root=...` d'un éventuel X-Ray trace header courant."""
    header = current_trace_header()
    if not header:
        return None
    for part in header.split(";"):
        kv = part.strip().split("=", 1)
        if len(kv) == 2 and kv[0] == "Root":
            return kv[1]
    return None


class NotifyFailureUseCase:
    """Use-case d'émission des `NotificationEvent`."""

    def __init__(
        self,
        *,
        publisher: INotificationPublisher,
        metrics_publish=None,
        metrics_throttled=None,
    ):
        """
        Args
        ----
        - `publisher` : implémentation `INotificationPublisher` (SQS).
        - `metrics_publish` : `Counter` Prometheus optionnel
          (`labels(category, outcome)`).
        - `metrics_throttled` : `Counter` Prometheus optionnel
          (`labels(failure_code)`).
        """
        self._publisher = publisher
        self._metrics_publish = metrics_publish
        self._metrics_throttled = metrics_throttled
        self._support_last_sent: dict[str, float] = {}

    async def user_untreatable(
        self,
        *,
        email: EmailMessage,
        code: FailureCode,
        missing_fields: list[MissingField] | None = None,
        rule_codes: list[FailureCode | str] | None = None,
        fare_event: dict | None = None,
        blocking_rules: list[str] | None = None,
        non_blocking_rules: list[str] | None = None,
        signals: list[str] | None = None,
        locale: str = "fr",
        next_action: NextAction = NextAction.reply_with_missing_info,
        human_summary: str | None = None,
        sqs_source_message_id: str | None = None,
        receive_count: int | None = None,
        severity: NotificationSeverity = NotificationSeverity.warning,
    ) -> None:
        """
        Émet un `NotificationEvent` `user_untreatable`.

        - `missing_fields` peut être passé directement (cas Ingestion).
        - Sinon, `rule_codes` (codes Tier 1) est résolu via le catalogue.
        """
        if not getattr(settings, "notifications_enabled", True):
            logger.debug("Notifications disabled, skipping user_untreatable")
            return

        try:
            mfields = list(missing_fields or [])
            if not mfields and rule_codes:
                mfields = build_missing_fields(
                    rule_codes, fare_event=fare_event, locale=locale
                )

            blocking = blocking_rules
            if blocking is None and rule_codes:
                blocking = [
                    c.value if isinstance(c, FailureCode) else str(c)
                    for c in rule_codes
                ]

            event = NotificationEvent.make_user_untreatable(
                service=settings.service_name,
                environment=settings.environment,
                failure_code=code,
                template_id=_user_template_for(code),
                sender=email.sender,
                locale=locale,
                missing_fields=mfields,
                blocking_rules=blocking,
                non_blocking_rules=non_blocking_rules,
                signals=signals,
                original_subject=email.subject,
                original_received_at=datetime.now(UTC).isoformat(),
                original_snippet=(email.body_text or None),
                human_summary=human_summary,
                next_action=next_action,
                support_contact=getattr(settings, "support_contact_email", None),
                source_message_id=email.thread.message_id if email.thread else None,
                trace_id=_trace_id_from_header(),
                correlation_id=email.thread.message_id if email.thread else None,
                sqs_source_message_id=sqs_source_message_id,
                receive_count=receive_count,
                severity=severity,
            )
            await self._publish(event)
        except Exception as exc:
            logger.error(
                "NotifyFailureUseCase.user_untreatable failed: code=%s err=%s",
                code.value,
                exc,
                exc_info=True,
            )
            self._mark_metric(category="user_untreatable", outcome="error")

    async def support_alert(
        self,
        *,
        code: FailureCode,
        error: ErrorInfo,
        source_artifact: SourceArtifact | None = None,
        sender: str | None = None,
        subject: str | None = None,
        source_message_id: str | None = None,
        sqs_source_message_id: str | None = None,
        receive_count: int | None = None,
        first_seen_at: str | None = None,
        human_summary: str | None = None,
        severity: NotificationSeverity = NotificationSeverity.error,
    ) -> None:
        """
        Émet un `NotificationEvent` `support_alert` (avec throttle).

        Le throttle est par `failure_code` : si la même alerte a été émise dans
        les `support_alert_throttle_seconds` dernières secondes, on saute.
        """
        if not getattr(settings, "notifications_enabled", True):
            logger.debug("Notifications disabled, skipping support_alert")
            return

        if self._is_throttled(code):
            logger.info(
                "support_alert throttled: code=%s ttl=%ss",
                code.value,
                getattr(settings, "support_alert_throttle_seconds", 300),
            )
            if self._metrics_throttled is not None:
                self._metrics_throttled.labels(failure_code=code.value).inc()
            return

        try:
            event = NotificationEvent.make_support_alert(
                service=settings.service_name,
                environment=settings.environment,
                failure_code=code,
                template_id=_support_template_for(code),
                error=error,
                source_artifact=source_artifact,
                sender=sender,
                subject=subject,
                source_message_id=source_message_id,
                trace_id=_trace_id_from_header(),
                correlation_id=source_message_id or sqs_source_message_id,
                sqs_source_message_id=sqs_source_message_id,
                receive_count=receive_count,
                first_seen_at=first_seen_at or datetime.now(UTC).isoformat(),
                runbook_url=_runbook_url_for(code),
                human_summary=human_summary,
                severity=severity,
            )
            await self._publish(event)
            self._support_last_sent[code.value] = time.monotonic()
        except Exception as exc:
            logger.error(
                "NotifyFailureUseCase.support_alert failed: code=%s err=%s",
                code.value,
                exc,
                exc_info=True,
            )
            self._mark_metric(category="support_alert", outcome="error")

    async def _publish(self, event: NotificationEvent) -> None:
        """Publie via le port + log + métrique."""
        await self._publisher.publish(event)
        logger.info(
            "NotificationEvent published: event_id=%s category=%s code=%s",
            event.event_id,
            event.category.value,
            event.failure_code.value,
        )
        self._mark_metric(category=event.category.value, outcome="success")

    def _is_throttled(self, code: FailureCode) -> bool:
        """Vrai si le code a été émis trop récemment (cache mémoire local)."""
        ttl = getattr(settings, "support_alert_throttle_seconds", 300)
        if ttl <= 0:
            return False
        last = self._support_last_sent.get(code.value)
        if last is None:
            return False
        return (time.monotonic() - last) < ttl

    def _mark_metric(self, *, category: str, outcome: str) -> None:
        """Incrémente le compteur publish si fourni."""
        if self._metrics_publish is None:
            return
        try:
            self._metrics_publish.labels(category=category, outcome=outcome).inc()
        except Exception as exc:  # pragma: no cover
            logger.debug("metrics increment failed: %s", exc)


def _runbook_url_for(code: FailureCode) -> str | None:
    """Construit l'URL du runbook depuis `support_runbook_base_url` + code."""
    base = getattr(settings, "support_runbook_base_url", None)
    if not base:
        return None
    return f"{base.rstrip('/')}/{code.value.lower()}"
