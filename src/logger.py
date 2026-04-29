"""Logging configuration for FairFare Ingestion Service."""

import json
import logging
import sys
from typing import Any

from config import settings


# ─────────────────────────────────────────────
# FORMATTER — JSON structuré
# ─────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "service": record.service,  # type: ignore[attr-defined]
            "environment": record.environment,  # type: ignore[attr-defined]
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, ensure_ascii=False)


# ─────────────────────────────────────────────
# FACTORY
# ─────────────────────────────────────────────
def build_logger(
    service_name: str,
    environment: str,
    log_level: str,
) -> logging.Logger:
    """Construit et retourne le logger applicatif."""
    log = logging.getLogger(service_name)

    if log.handlers:
        return log  # Évite la duplication en cas de re-import

    log.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log.addHandler(handler)
    log.propagate = False

    # Injection des métadonnées fixes dans le logger
    old_factory = logging.getLogRecordFactory()

    def record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = old_factory(*args, **kwargs)
        record.service = service_name  # type: ignore[attr-defined]
        record.environment = environment  # type: ignore[attr-defined]
        return record

    logging.setLogRecordFactory(record_factory)

    return log


# ─────────────────────────────────────────────
# INSTANCE GLOBALE — initialisée depuis config
# ─────────────────────────────────────────────
logger = build_logger(settings.service_name, settings.environment, settings.log_level)
