"""
Value object `ErrorInfo`.

Rôle
----
Représenter de manière structurée une erreur applicative pour qu'elle soit
exploitable côté support :
- classe et message → identification rapide
- module / file / line / function → localisation dans le code
- stack → contexte complet (tronqué pour respecter la limite SQS 256 KB)

L'extraction depuis une exception Python vit dans la couche Infrastructure
(`infrastructure/error_collection/extractors.py`) car elle dépend des détails
techniques de Python (`traceback.extract_tb`). Ce VO reste pur et serializable.
"""

from __future__ import annotations

from dataclasses import dataclass

STACK_MAX_CHARS = 4000
MESSAGE_MAX_CHARS = 1000


@dataclass(frozen=True)
class ErrorInfo:
    """Erreur applicative sérialisable pour les `support_alert`."""

    class_: str
    message: str
    module: str | None = None
    file: str | None = None
    line: int | None = None
    function: str | None = None
    stack: str | None = None

    def to_dict(self) -> dict:
        """Sérialise en dict pour `variables.error`."""
        return {
            "class": self.class_,
            "message": self.message,
            "module": self.module,
            "file": self.file,
            "line": self.line,
            "function": self.function,
            "stack": self.stack,
        }
