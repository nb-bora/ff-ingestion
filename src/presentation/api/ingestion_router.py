"""
Routes HTTP (FastAPI) exposées par le microservice.

Contrat exposé
--------------
- `GET /` : métadonnées service
- `GET /health` : health check unique (live + ready)
- `GET /metrics` : métriques Prometheus
- `POST /parse` : parse une demande de voyage

Utilisé par
---------
- `src/main.py` (`app.include_router(router)`)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse, Response

from application.services.ingestion_service import IngestionService
from config import settings
from domain.entities.email_message import EmailMessage
from domain.value_objects.email_metadata import EmailThreadMetadata
from logger import logger
from presentation.api.dependencies import get_ingestion_service, get_metrics, get_sqs_consumer
from presentation.api.metrics import Metrics, parse_duration, render_prometheus
from presentation.schemas.parse_request_schema import ParseRequestSchema
from presentation.schemas.parse_response_schema import ParseResponseSchema
from shared.constants import SERVICE_DISPLAY_NAME, SERVICE_VERSION
from shared.email_utils import parse_eml_bytes
from shared.exceptions import MissingSenderError
from shared.utils import looks_like_raw_email
from xray_config import xray_capture

router = APIRouter()


@router.get("/")
async def root():
    """Endpoint racine (métadonnées service)."""
    return {
        "service": SERVICE_DISPLAY_NAME,
        "version": SERVICE_VERSION,
        "status": "running",
        "docs": "/docs",
    }


@router.get("/health")
async def health_check():
    """
    Health check unique (live + ready).

    - **Live**: si ce endpoint répond, le process est vivant.
    - **Ready**: vérifie les dépendances critiques sans appels réseau coûteux.

    Codes
    -----
    - 200 si tout est prêt.
    - 503 si une dépendance critique est en panne.
    """
    metrics = get_metrics()
    checks: dict[str, bool] = {}

    if settings.consumer_enabled:
        try:
            consumer = get_sqs_consumer()
            checks["consumer_running"] = consumer.is_healthy()
        except Exception as e:
            logger.warning("Consumer health check failed: %s", e)
            checks["consumer_running"] = False
    else:
        checks["consumer_running"] = True

    checks["openai_configured"] = bool(settings.get_openai_api_key())
    checks["sqs_email_queue_configured"] = bool(settings.sqs_email_queue_url)
    checks["sqs_fare_event_queue_configured"] = bool(
        settings.sqs_fare_event_queue_url
    )

    all_ok = all(checks.values())
    payload = {
        "status": "healthy" if all_ok else "unhealthy",
        "service": SERVICE_DISPLAY_NAME,
        "version": SERVICE_VERSION,
        "environment": settings.environment,
        "checks": checks,
        "metrics": {
            "messages_processed": metrics.messages_processed,
            "errors": metrics.errors,
        },
    }
    return JSONResponse(
        status_code=status.HTTP_200_OK
        if all_ok
        else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload,
    )


@router.get("/metrics")
async def get_metrics_endpoint():
    """
    Expose les métriques au format Prometheus.
    """
    payload, content_type = render_prometheus()
    return Response(content=payload, media_type=content_type)


@router.post("/parse", response_model=ParseResponseSchema)
@xray_capture("ingestion_parse")
async def parse_airfare(
    request: ParseRequestSchema,
    metrics: Metrics = Depends(get_metrics),
    service: IngestionService = Depends(get_ingestion_service),
):
    """
    Parse un email/texte en `FareEvent` (via OpenAI) et retourne un ID.

    Entrées
    -------
    - `request.email_body` (obligatoire)
    - `request.sender` (optionnel)

    Sortie
    ------
    - `{ fare_event_id, status="parsed" }` si le parsing réussit.

    Erreurs
    -------
    - 400: `email_body` vide
    - 400: sender introuvable
    - 500: erreur inattendue

    Impact
    ------
    - Incrémente les métriques in-memory.
    - **Ne publie pas** sur SQS (le consumer s'en charge sur le chemin SES).
    """
    if not request.email_body or not request.email_body.strip():
        metrics.increment_error()
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "email_body cannot be empty"},
        )

    sender = request.sender
    subject = None
    thread = EmailThreadMetadata()

    if looks_like_raw_email(request.email_body):
        try:
            parsed = parse_eml_bytes(request.email_body.encode("utf-8"))
            sender = parsed.get("from_email") or sender
            subject = parsed.get("subject") or subject
            thread = EmailThreadMetadata(
                message_id=parsed.get("message_id"),
                in_reply_to=parsed.get("in_reply_to"),
                references=parsed.get("references"),
                reply_to=parsed.get("reply_to"),
            )
        except Exception as e:
            logger.warning("Failed to parse EML; treating as plain text: %s", e)

    email = EmailMessage(
        sender=sender or "",
        subject=subject,
        body_text=request.email_body,
        thread=thread,
    )

    try:
        with parse_duration.time():
            fare_event = await service.parse_email_use_case.execute(email)
        logger.info("Parsed fare event: %s", fare_event.get("id"))
        metrics.increment_processed()
        return ParseResponseSchema(
            fare_event_id=fare_event.get("id"), status="parsed"
        )
    except MissingSenderError:
        metrics.increment_error(outcome="missing_sender")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "Cannot extract sender email from message"},
        )
    except Exception as e:
        logger.error("Parse error: %s", e, exc_info=True)
        metrics.increment_error(outcome="failed")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to parse airfare"},
        )
