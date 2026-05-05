"""
Schéma Pydantic du `NotificationEvent`.

Rôle
----
Valider de manière **non bloquante** les payloads `NotificationEvent` avant
publication SQS, et offrir un point de sérialisation lisible côté tests
(import-friendly).

Pourquoi non bloquant ?
-----------------------
La validation est un **garde-fou** qualité : si un champ obligatoire venait à
manquer suite à une régression, on log un warning + métrique mais on publie
quand même (ne pas perdre l'event). Le notifier saura tolérer/router.

Ce schéma documente aussi le **contrat partagé** avec ff-notifier et
ff-intelligence-engine. Il est dérivé du Domain (`NotificationEvent`) et ne
doit jamais s'en éloigner.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class MissingFieldSchema(BaseModel):
    """Schéma d'un élément `variables.missing_fields[]`."""

    code: str
    path: str
    label: str
    expected: str | None = None
    found: str | None = None
    fix_hint: str | None = None


class OriginalEmailSchema(BaseModel):
    """Schéma de `variables.original_email`."""

    subject: str | None = None
    received_at: str | None = None
    snippet: str | None = None


class UserUntreatableVariablesSchema(BaseModel):
    """Schéma `variables` quand `category == user_untreatable`."""

    user_first_name: str | None = None
    original_email: OriginalEmailSchema = Field(default_factory=OriginalEmailSchema)
    missing_fields: list[MissingFieldSchema] = Field(default_factory=list)
    blocking_rules: list[str] = Field(default_factory=list)
    non_blocking_rules: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    human_summary: str | None = None
    next_action: str | None = None
    support_contact: str | None = None


class ErrorSchema(BaseModel):
    """Schéma de `variables.error`."""

    class_: str = Field(alias="class")
    message: str
    module: str | None = None
    file: str | None = None
    line: int | None = None
    function: str | None = None
    stack: str | None = None

    model_config = {"populate_by_name": True}


class OccurrenceSchema(BaseModel):
    """Schéma de `variables.occurrence`."""

    trace_id: str | None = None
    host: str | None = None
    deploy_sha: str | None = None
    receive_count: int | None = None
    first_seen_at: str | None = None


class SourceArtifactSchema(BaseModel):
    """Schéma de `variables.source_artifact`."""

    kind: str = "sqs_message"
    queue_url: str | None = None
    sqs_message_id: str | None = None
    receipt_handle_redacted: str | None = None
    raw_body_excerpt: str | None = None
    ses_message_id: str | None = None
    sender: str | None = None
    subject: str | None = None
    size_bytes: int | None = None


class SupportAlertVariablesSchema(BaseModel):
    """Schéma `variables` quand `category == support_alert`."""

    error: ErrorSchema
    occurrence: OccurrenceSchema = Field(default_factory=OccurrenceSchema)
    source_artifact: SourceArtifactSchema | None = None
    runbook_url: str | None = None
    human_summary: str | None = None


class NotificationRecipientSchema(BaseModel):
    """Schéma de `recipient`."""

    type: Literal["user", "support"]
    email: str | None = None
    locale: str = "fr"


class NotificationContextSchema(BaseModel):
    """Schéma de `context`."""

    sender: str | None = None
    subject: str | None = None
    source_message_id: str | None = None
    received_at: str | None = None
    trace_id: str | None = None
    correlation_id: str | None = None
    sqs_source_message_id: str | None = None
    receive_count: int | None = None


class NotificationEventSchema(BaseModel):
    """
    Schéma de l'événement publié sur la queue notifications.

    Le bloc `variables` est laissé en `dict` à ce niveau pour permettre la
    coexistence des deux schémas (`UserUntreatableVariablesSchema` et
    `SupportAlertVariablesSchema`) ; on valide la bonne forme via le validateur
    discriminé selon `category`.
    """

    schema_version: int = 1
    event_id: str
    occurred_at: str
    service: str
    environment: str
    category: Literal["user_untreatable", "support_alert"]
    severity: Literal["info", "warning", "error", "critical"]
    template_id: str
    failure_code: str
    recipient: NotificationRecipientSchema
    context: NotificationContextSchema = Field(
        default_factory=NotificationContextSchema
    )
    variables: dict[str, Any] = Field(default_factory=dict)
