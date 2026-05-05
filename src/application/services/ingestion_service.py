"""
Facade d'assemblage de la couche Application.

Rôle
----
- Construire des use-cases prêts à l'emploi à partir d'implémentations
  concrètes (parser + publisher + notification publisher).

Utilisé par
---------
- `presentation.api.dependencies.get_ingestion_service` (composition root)

Pourquoi une facade ?
--------------------
Pour que Presentation/Infrastructure n'ait pas à connaître les détails des
constructeurs de use-cases et leurs dépendances internes.
"""

from __future__ import annotations

from dataclasses import dataclass

from application.interfaces.email_parser import IEmailParser
from application.interfaces.message_publisher import IMessagePublisher
from application.interfaces.notification_publisher import INotificationPublisher
from application.use_cases.notify_failure_use_case import NotifyFailureUseCase
from application.use_cases.parse_email_use_case import ParseEmailUseCase
from application.use_cases.process_email_use_case import ProcessEmailUseCase


@dataclass(frozen=True)
class IngestionService:
    """Facade d'orchestration (Application) : construit les use-cases."""

    parse_email_use_case: ParseEmailUseCase
    process_email_use_case: ProcessEmailUseCase
    notify_failure_use_case: NotifyFailureUseCase

    @staticmethod
    def build(
        *,
        parser: IEmailParser,
        publisher: IMessagePublisher,
        notification_publisher: INotificationPublisher,
        metrics_publish=None,
        metrics_throttled=None,
    ) -> "IngestionService":
        """Factory de composition.

        Args
        ----
        - `parser` : implémentation de `IEmailParser` (OpenAI…)
        - `publisher` : publisher de `FareEvent` downstream
        - `notification_publisher` : publisher SQS sur la queue notifications
        - `metrics_publish` / `metrics_throttled` : compteurs Prometheus
          optionnels injectés dans `NotifyFailureUseCase`.
        """
        notify_uc = NotifyFailureUseCase(
            publisher=notification_publisher,
            metrics_publish=metrics_publish,
            metrics_throttled=metrics_throttled,
        )
        parse_uc = ParseEmailUseCase(parser=parser)
        process_uc = ProcessEmailUseCase(
            parse_email=parse_uc,
            publisher=publisher,
            notify_failure=notify_uc,
        )
        return IngestionService(
            parse_email_use_case=parse_uc,
            process_email_use_case=process_uc,
            notify_failure_use_case=notify_uc,
        )
