"""
Facade d’assemblage de la couche Application.

Rôle
----
- Construire des use-cases prêts à l’emploi à partir d’implémentations
  concrètes (parser/publisher).

Utilisé par
---------
- `presentation.api.dependencies.get_ingestion_service` (composition root)

Pourquoi une facade ?
--------------------
Pour que Presentation/Infrastructure n’ait pas à connaître les détails des
constructeurs de use-cases et leurs dépendances internes.
"""

from __future__ import annotations

from dataclasses import dataclass

from application.interfaces.email_parser import IEmailParser
from application.use_cases.parse_email_use_case import ParseEmailUseCase
from application.use_cases.process_email_use_case import ProcessEmailUseCase


@dataclass(frozen=True)
class IngestionService:
    """
    Facade d’orchestration (Application) : construit les use-cases.
    """

    parse_email_use_case: ParseEmailUseCase
    process_email_use_case: ProcessEmailUseCase

    @staticmethod
    def build(*, parser: IEmailParser) -> IngestionService:
        """
        Factory de composition.

        Utilise
        -------
        - `ParseEmailUseCase(parser=...)`
        - `ProcessEmailUseCase(parse_email=...)`
        """
        parse_uc = ParseEmailUseCase(parser=parser)
        process_uc = ProcessEmailUseCase(parse_email=parse_uc)
        return IngestionService(
            parse_email_use_case=parse_uc,
            process_email_use_case=process_uc,
        )
