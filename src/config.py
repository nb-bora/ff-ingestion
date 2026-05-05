"""
Configuration applicative (Settings) du microservice.

Rôle
----
- Centraliser toutes les variables d'environnement (service, AWS, SQS, OpenAI, X-Ray).
- Résoudre la clé OpenAI **à la première utilisation** depuis:
  - `OPENAI_API_KEY` (env)
  - ou AWS Secrets Manager (si `SECRETS_MANAGER_ENABLED=true`)

Utilisé par
---------
- Tous les modules via `from config import settings`
- Démarrage serveur (host/port) via `run.py` / uvicorn
- Clients AWS/OpenAI (région, profil, URLs de queues, etc.)

Impact / effets de bord
----------------------
- L'instanciation de `Settings` ne fait **aucun appel réseau**.
- L'appel à `settings.get_openai_api_key()` peut faire un appel à AWS
  Secrets Manager (timeout 5s, max_attempts 3). Le résultat est mémoïsé.
"""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Optional

from pydantic import Field, PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Paramètres applicatifs chargés depuis les variables d'environnement.

    Note
    ----
    `model_config` charge `.env` depuis la racine du repo.
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
    aws_region: str = Field(default="us-east-1")
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

    # ── SQS — Heartbeat (extension du visibility timeout pendant le parse) ──
    sqs_heartbeat_interval_seconds: int = Field(default=60)
    sqs_heartbeat_extend_seconds: int = Field(default=120)

    # ── SQS — FIFO ───────────────────────────
    parsed_sqs_message_group_id: str = Field(default="default")

    # ── OpenAI ───────────────────────────────
    openai_api_key: str = Field(default="")
    openai_model: str = Field(default="gpt-4o-mini")
    openai_timeout_seconds: float = Field(default=20.0)
    openai_max_retries: int = Field(default=2)
    secrets_manager_enabled: bool = Field(default=False)
    openai_secret_name: str = Field(default="fairfare/openai-api-key")

    # ── Consumer ─────────────────────────────
    consumer_enabled: bool = Field(default=True)
    consumer_max_retries: int = Field(default=3)
    consumer_error_delay_seconds: int = Field(default=5)

    # ── X-Ray ────────────────────────────────
    enable_xray: bool = Field(default=False)
    aws_xray_daemon_address: str = Field(default="127.0.0.1:2000")

    # ── Logs ─────────────────────────────────
    # Log le contenu intégral des réponses OpenAI (DEV uniquement).
    log_openai_payload: bool = Field(default=False)

    # ─────────────────────────────────────────
    # RÉSOLUTION LAZY DE LA CLÉ OPENAI
    # ─────────────────────────────────────────
    # Cache et verrou: l'appel à Secrets Manager se fait au plus une fois.
    _openai_key_cache: Optional[str] = PrivateAttr(default=None)
    _openai_key_lock: Lock = PrivateAttr(default_factory=Lock)

    def get_openai_api_key(self) -> str:
        """
        Retourne la clé OpenAI, résolue paresseusement.

        Ordre de résolution
        -------------------
        1. `OPENAI_API_KEY` (env / `.env`)
        2. AWS Secrets Manager si `SECRETS_MANAGER_ENABLED=true`
        3. Chaîne vide → mode dégradé (OpenAI absent)

        Note
        ----
        - La résolution est mémoïsée: un seul appel réseau par process.
        - Aucune exception levée: en cas d'échec Secrets Manager, on
          retourne "" et un warning est loggé. Le caller (parser) décide.
        """
        if self.openai_api_key:
            return self.openai_api_key

        if self._openai_key_cache is not None:
            return self._openai_key_cache

        with self._openai_key_lock:
            if self._openai_key_cache is not None:
                return self._openai_key_cache

            resolved = ""
            if self.secrets_manager_enabled:
                # Import différé pour éviter d'importer boto3 si secrets_manager
                # est désactivé (et pour casser les cycles d'import potentiels).
                from infrastructure.aws.secrets_manager import (
                    extract_secret_key,
                    get_secret,
                )

                secret_value = get_secret(
                    secret_name=self.openai_secret_name,
                    region_name=self.aws_region,
                    profile_name=self.aws_profile,
                )
                if secret_value:
                    resolved = extract_secret_key(secret_value)

            self._openai_key_cache = resolved
            return resolved


# ─────────────────────────────────────────────
# INSTANCE GLOBALE
# ─────────────────────────────────────────────
settings = Settings()
