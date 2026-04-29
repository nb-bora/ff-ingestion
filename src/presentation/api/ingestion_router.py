"""
Routes HTTP (FastAPI) exposées par le microservice.

Contrat exposé
--------------
- `GET /` : métadonnées service
- `GET /health` : santé + config + métriques
- `GET /metrics` : métriques in-memory
- `POST /parse` : parse une demande de voyage

Parité avec `ff-ingestion`
--------------------------
- Même structure JSON de réponse pour `GET /` et `GET /health`
- Même comportement d’erreur:
  - 400 si `email_body` vide
  - 400 si on ne peut pas déterminer un sender
  - 500 pour les autres erreurs
- Même heuristique "email brut RFC822": on tente d’extraire `From/Subject` etc.

Utilisé par
---------
- `src/main.py` (`app.include_router(router)`)

Utilise
-------
- `presentation.api.dependencies.get_ingestion_service` (use-cases)
- `presentation.api.dependencies.get_metrics` (métriques in-memory)
- `shared.email_utils.parse_eml_bytes` (parse EML)
- `domain.entities.EmailMessage` (objet d’entrée Application)
"""

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from config import settings
from domain.entities.email_message import EmailMessage
from domain.value_objects.email_metadata import EmailThreadMetadata
from logger import logger
from presentation.api.dependencies import get_ingestion_service, get_metrics
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
    """
    Endpoint racine.

    Impact
    ------
    - Aucun side-effect; pure réponse JSON.
    """
    return {
        "service": SERVICE_DISPLAY_NAME,
        "version": SERVICE_VERSION,
        "status": "running",
        "docs": "/docs",
    }


@router.get("/health")
async def health_check():
    """
    Health check (ECS/ALB friendly).

    Utilise
    -------
    - `config.settings` (info env)
    - `get_metrics()` (counters in-memory)

    Note
    ----
    Le champ `openai_configured` ne valide pas la capacité réelle à parser (quota),
    uniquement la présence d’une clé.
    """
    metrics = get_metrics()
    return {
        "status": "healthy",
        "service": "ff-ingestion",
        "version": SERVICE_VERSION,
        "environment": settings.environment,
        "consumer_enabled": settings.consumer_enabled,
        "aws_region": settings.aws_region,
        "aws_profile": settings.aws_profile or "(default)",
        "openai_configured": bool(settings.openai_api_key),
        "metrics": {
            "messages_processed": metrics.messages_processed,
            "errors": metrics.errors,
        },
    }


@router.get("/metrics")
async def get_metrics_endpoint():
    """
    Expose les métriques in-memory.

    Utilisé par
    ---------
    - Debug local / monitoring simple
    """
    metrics = get_metrics()
    return {"messages_processed": metrics.messages_processed, "errors": metrics.errors}


@router.post("/parse", response_model=ParseResponseSchema)
@xray_capture("ingestion_parse")
async def parse_airfare(request: ParseRequestSchema):
    """
    Parse un email/texte en `FareEvent` (via OpenAI) et retourne un ID.

    Entrées
    -------
    - `request.email_body` (obligatoire)
    - `request.sender` (optionnel)

    Sortie
    ------
    - `{ fare_event_id, status="published" }` si le parsing est tenté avec succès.

    Erreurs (parité `ff-ingestion`)
    -------------------------------
    - 400: `email_body` vide
    - 400: sender introuvable (impossible de traiter/répondre downstream)
    - 500: erreur inattendue

    Utilise
    -------
    - `shared.utils.looks_like_raw_email` + `shared.email_utils.parse_eml_bytes`
      pour extraire sender/subject/threading d’un email brut.
    - `application.use_cases.ParseEmailUseCase` via `IngestionService`

    Impact
    ------
    - Incrémente les métriques in-memory.
    - **Ne publie pas** sur SQS (parité `ff-ingestion`).
    """
    metrics = get_metrics()

    if not request.email_body or not request.email_body.strip():
        metrics.increment_error()
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "email_body cannot be empty"},
        )

    sender = request.sender
    subject = None
    thread = EmailThreadMetadata()

    # Align ff-ingestion: si on reçoit un EML brut, essayer d'extraire sender/subject/threading
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
        service = get_ingestion_service()
        fare_event = await service.parse_email_use_case.execute(email)
        logger.info("Parsed fare event: %s", fare_event.get("id"))
        metrics.increment_processed()
        return ParseResponseSchema(
            fare_event_id=fare_event.get("id"), status="published"
        )
    except MissingSenderError:
        metrics.increment_error()
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "Cannot extract sender email from message"},
        )
    except Exception as e:
        logger.error("Parse error: %s", e, exc_info=True)
        metrics.increment_error()
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to parse airfare"},
        )
