"""
Point d’entrée FastAPI du microservice **Ingestion**.

Rôle
----
- Assembler la couche Presentation (routes) et démarrer/arrêter les ressources
  longues durées (consumer SQS) via `lifespan`.
- Appliquer des comportements de parité avec `ff-ingestion` (notamment:
  transformer les erreurs de validation Pydantic en HTTP 400 au lieu de 422).

Utilisé par
---------
- `uvicorn main:app --app-dir src` (local)
- Docker CMD (voir `Dockerfile` / `Dockerfile.dev`)

Utilise
-------
- `presentation.api.ingestion_router.router` (endpoints HTTP)
- `presentation.api.dependencies.get_sqs_consumer` (instanciation consumer)
- `xray_config.init_xray` (observabilité)
- `config.settings` (flags de configuration)

Impact / effets de bord
----------------------
- Au démarrage: peut lancer un polling SQS si `CONSUMER_ENABLED=true`.
- Au shutdown: arrête le consumer et libère ses ressources.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from config import settings
from logger import logger
from presentation.api.dependencies import get_sqs_consumer
from presentation.api.ingestion_router import router as ingestion_router
from xray_config import init_xray

init_xray()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    Gère le cycle de vie applicatif.

    Utilisé par
    ---------
    - `FastAPI(..., lifespan=lifespan)`

    Utilise
    -------
    - `presentation.api.dependencies.get_sqs_consumer`
    - `infrastructure.messaging.sqs_consumer.SQSConsumer.start/stop`

    Impact
    ------
    - Démarre/arrête le consumer SQS selon `settings.consumer_enabled`.
    """
    logger.info("Starting FairFare Ingestion Service")
    logger.info("Consumer enabled: %s", settings.consumer_enabled)
    consumer = None
    try:
        if settings.consumer_enabled:
            consumer = get_sqs_consumer()
            await consumer.start()
            logger.info("SQS Consumer started")
        else:
            logger.info("SQS Consumer is disabled")
        logger.info("Application startup complete")
    except Exception as e:
        logger.error("Error during startup: %s", e, exc_info=True)
        raise

    yield

    logger.info("Shutting down")
    if consumer:
        await consumer.stop()
    logger.info("Shutdown complete")


app = FastAPI(
    title="FairFare Ingestion Service",
    description="Parse airfare submissions from emails and API",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(ingestion_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request, exc):
    """
    Convertit les erreurs de validation Pydantic en HTTP 400 (parité `ff-ingestion`).

    Pourquoi
    -------
    FastAPI retourne typiquement 422 sur validation. `ff-ingestion` force 400,
    donc ce handler garantit un contrat identique pour les clients.

    Utilisé par
    ---------
    - FastAPI exception handling pipeline

    Impact
    ------
    - Modifie uniquement le code HTTP et le JSON de réponse (`{"error": ...}`).
    """
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": str(exc)},
    )
