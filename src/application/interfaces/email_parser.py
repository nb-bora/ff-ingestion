"""
Contrat Application: parsing d’un email.

Ce module définit une abstraction (`Protocol`) pour découpler Application et
Infrastructure.

Utilisé par
---------
- `application.use_cases.ParseEmailUseCase`

Implémentations
--------------
- `infrastructure.parsers.openai_email_parser.OpenAIEmailParser`
"""

from __future__ import annotations

from typing import Protocol

from domain.entities.email_message import EmailMessage


class IEmailParser(Protocol):
    async def parse(
        self, email: EmailMessage
    ) -> tuple[dict, str | None, list[str] | None]:
        """
        Parse un `EmailMessage` et retourne un triplet.

        Retour
        ------
        extracted_travel:
            dict JSON sérialisable (peut être `{}`).
        openai_response_id:
            identifiant de réponse OpenAI (ou `None`).
        failure_reasons:
            raisons "humaines" si la requête n’est pas assez complète (ou `None`).

        Notes de parité
        --------------
        `ff-ingestion` produit des reasons dans certains cas (missing fields /
        extraction invalide). L’implémentation OpenAI doit respecter cette
        convention.
        """
