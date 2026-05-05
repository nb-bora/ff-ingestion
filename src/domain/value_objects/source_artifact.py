"""
Value object `SourceArtifact`.

Rôle
----
Désigner le **fichier/payload concerné** qui a déclenché l'erreur, pour que le
support puisse :
- retrouver le mail brut (`ses_message_id` → S3 si bucket SES configuré),
- consulter immédiatement un extrait du body (`raw_body_excerpt`),
- tracer l'origine SQS exacte (`queue_url` + `sqs_message_id`).

Le `receipt_handle` est intentionnellement **tronqué/redacté** (sécurité : il
permet de modifier la visibilité d'un message SQS si exposé).

Limites
-------
- `raw_body_excerpt` ≤ 1024 caractères (constante `RAW_BODY_MAX_CHARS`)
- Le full body reste disponible dans CloudWatch (logs structurés du consumer).
"""

from __future__ import annotations

from dataclasses import dataclass

RAW_BODY_MAX_CHARS = 1024


@dataclass(frozen=True)
class SourceArtifact:
    """Référence à la source SQS/SES qui a causé l'erreur."""

    kind: str = "sqs_message"
    queue_url: str | None = None
    sqs_message_id: str | None = None
    receipt_handle_redacted: str | None = None
    raw_body_excerpt: str | None = None
    ses_message_id: str | None = None
    sender: str | None = None
    subject: str | None = None
    size_bytes: int | None = None

    def to_dict(self) -> dict:
        """Sérialise en dict pour `variables.source_artifact`."""
        return {
            "kind": self.kind,
            "queue_url": self.queue_url,
            "sqs_message_id": self.sqs_message_id,
            "receipt_handle_redacted": self.receipt_handle_redacted,
            "raw_body_excerpt": self.raw_body_excerpt,
            "ses_message_id": self.ses_message_id,
            "sender": self.sender,
            "subject": self.subject,
            "size_bytes": self.size_bytes,
        }
