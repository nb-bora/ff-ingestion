"""
Configuration applicative (Settings) du microservice.

Rôle
----
- Centraliser toutes les variables d’environnement (service, AWS, SQS, OpenAI, X-Ray).
- Résoudre la clé OpenAI depuis:
  - `OPENAI_API_KEY` (env)
  - ou AWS Secrets Manager (si `SECRETS_MANAGER_ENABLED=true`)

Utilisé par
---------
- Tous les modules via `from config import settings`
- Démarrage serveur (host/port) via `run.py` / uvicorn
- Clients AWS/OpenAI (région, profil, URLs de queues, etc.)

Impact / effets de bord
----------------------
- Lors de l’instanciation de `Settings`, peut appeler AWS Secrets Manager
  (si activé) et donc nécessiter des credentials AWS.
"""

import json
from pathlib import Path
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ─────────────────────────────────────────────
# AWS SECRETS MANAGER
# ─────────────────────────────────────────────
def get_secret(
    secret_name: str,
    region_name: str,
    profile_name: Optional[str] = None,
) -> str:
    """
    Récupère un secret depuis AWS Secrets Manager.

    Utilisé par
    ---------
    - `Settings.resolve_openai_api_key` quand `secrets_manager_enabled=true`

    Impact
    ------
    - Appel réseau AWS (Secrets Manager).
    - Lève `RuntimeError` avec un message explicite si le secret est absent ou
      si l’accès est refusé.
    """
    try:
        session = (
            boto3.Session(profile_name=profile_name)
            if profile_name
            else boto3.Session()
        )
        client = session.client("secretsmanager", region_name=region_name)
        response = client.get_secret_value(SecretId=secret_name)

        if "SecretString" in response:
            return response["SecretString"]

        return response["SecretBinary"].decode("utf-8")

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        messages: dict[str, str] = {
            "ResourceNotFoundException": f"Secret '{secret_name}' introuvable dans la région '{region_name}'",
            "AccessDeniedException": f"Accès refusé au secret '{secret_name}'",
        }
        raise RuntimeError(
            messages.get(error_code, f"Erreur Secrets Manager : {e}")
        ) from e


# ─────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────
class Settings(BaseSettings):
    """
    Paramètres applicatifs chargés depuis les variables d'environnement.

    Utilisé par
    ---------
    - `main.py` (lifespan, flags consumer, env)
    - `infrastructure.messaging` (SQS URLs, région, profil)
    - `infrastructure.parsers` (OpenAI model/key)
    - `infrastructure.aws.xray_config` (enable_xray, daemon address)

    Note
    ----
    La `model_config` charge `.env` depuis la racine du repo.
    """

    model_config = SettingsConfigDict(
        # `src/config.py` → parents[1] = racine du repo (Ingestion/)
        env_file=Path(__file__).parents[1] / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Service ──────────────────────────────
    service_name: str = Field(default="ff-ingestion")
    environment: str = Field(default="dev")
    log_level: str = Field(default="INFO")
    host: str = Field(default="0.0.0.0")  # nosec B104
    port: int = Field(default=8000)

    # ── AWS ──────────────────────────────────
    aws_region: str = Field(default="af-south-1")
    aws_profile: Optional[str] = Field(default=None)

    # ── SQS — Queues ─────────────────────────
    sqs_email_queue_url: str = Field(default="")
    sqs_fare_event_queue_url: str = Field(default="")

    # ── SQS — Paramètres ─────────────────────
    sqs_max_workers: int = Field(default=2)
    sqs_max_messages: int = Field(default=10)
    sqs_wait_time_seconds: int = Field(default=20)
    sqs_visibility_timeout: int = Field(default=300)
    sqs_max_concurrent_messages: int = Field(default=10)

    # ── SQS — FIFO ───────────────────────────
    parsed_sqs_message_group_id: str = Field(default="default")

    # ── OpenAI ───────────────────────────────
    openai_api_key: str = Field(default="")
    openai_model: str = Field(default="gpt-4o-mini")
    secrets_manager_enabled: bool = Field(default=False)
    openai_secret_name: str = Field(default="fairfare/openai-api-key")

    # ── Consumer ─────────────────────────────
    consumer_enabled: bool = Field(default=True)
    consumer_max_retries: int = Field(default=3)
    consumer_error_delay_seconds: int = Field(default=5)

    # ── X-Ray ────────────────────────────────
    enable_xray: bool = Field(default=False)
    aws_xray_daemon_address: str = Field(default="127.0.0.1:2000")

    # ─────────────────────────────────────────
    # VALIDATORS
    # ─────────────────────────────────────────
    @model_validator(mode="after")
    def resolve_openai_api_key(self) -> "Settings":
        """
        Résout `openai_api_key` (env > Secrets Manager).

        Parité `ff-ingestion`
        ---------------------
        Le service doit pouvoir démarrer même sans OpenAI en dev; dans ce cas
        `openai_api_key` reste vide et `/health` expose `openai_configured=false`.

        Utilise
        -------
        - `get_secret` si `secrets_manager_enabled=true`
        """
        if self.openai_api_key:
            return self

        # Aligné sur ff-ingestion: l’app peut démarrer sans OpenAI (mode dégradé).
        if not self.secrets_manager_enabled:
            return self

        secret_value = get_secret(
            self.openai_secret_name,
            self.aws_region,
            self.aws_profile,
        )

        try:
            secret_dict: dict[str, Any] = json.loads(secret_value)
            self.openai_api_key = (
                secret_dict.get("api_key")
                or secret_dict.get("OPENAI_API_KEY")
                or secret_dict.get("key")
                or secret_value
            )
        except (json.JSONDecodeError, TypeError):
            self.openai_api_key = secret_value

        return self


# ─────────────────────────────────────────────
# INSTANCE GLOBALE
# ─────────────────────────────────────────────
settings = Settings()
