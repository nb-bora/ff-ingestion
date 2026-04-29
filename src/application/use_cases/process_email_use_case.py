"""
Use-case Application: traitement d’un email côté consumer (parse uniquement).

Rôle
----
- Utiliser `ParseEmailUseCase` pour produire un `FareEvent`

Utilisé par
---------
- `infrastructure.messaging.SQSConsumer` (par message)

Utilise
-------
- `shared.exceptions.MissingSenderError`

Impact
------
- Aucun side-effect réseau: la publication est volontairement faite en Infrastructure
  (dans `SQSConsumer`) afin de permettre des ajustements post-parse avant publish.
"""

from __future__ import annotations

from application.use_cases.parse_email_use_case import ParseEmailUseCase
from domain.entities.email_message import EmailMessage
from shared.exceptions import MissingSenderError


class ProcessEmailUseCase:
    """
    Use-case orienté "consumer": parse + publish + retourne l’event.
    L’effacement du message SQS (delete) est géré en Infrastructure (consumer).
    """

    def __init__(self, *, parse_email: ParseEmailUseCase):
        """
        Paramètres
        ----------
        parse_email:
            Use-case de parsing (sans side-effect).
        """
        self._parse_email = parse_email

    async def execute(self, email: EmailMessage) -> dict:
        """
        Exécute le traitement "consumer".

        Étapes
        ------
        - Valide le sender
        - Parse (produit `FareEvent`)
        - Publish (SQS)

        Erreurs
        ------
        - `MissingSenderError` si sender absent.
        """
        if not email.sender:
            raise MissingSenderError("No sender email found in message")
        return await self._parse_email.execute(email)
