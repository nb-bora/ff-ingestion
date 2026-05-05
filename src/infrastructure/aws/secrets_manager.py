"""
Helper unique pour AWS Secrets Manager.

Conception
----------
- Une seule implémentation `get_secret` (anciennement dupliquée dans `config.py`).
- Les appels réseau sont bornés par des timeouts et un nombre de retries
  raisonnables, pour éviter qu'un secret manager indisponible ne bloque le
  démarrage du service ou un parse en cours.
- En cas d'erreur, on retourne `None` et on log un warning. Le caller décide
  s'il considère cela comme fatal (ex. mode dégradé "OpenAI absent").

Utilisé par
---------
- `config.Settings.get_openai_api_key` (résolution lazy de la clé OpenAI).
"""

from __future__ import annotations

import json
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from logger import logger

_BOTO_CONFIG = Config(
    connect_timeout=2,
    read_timeout=5,
    retries={"max_attempts": 3, "mode": "standard"},
)


def get_secret(
    *,
    secret_name: str,
    region_name: str,
    profile_name: str | None = None,
) -> str | None:
    """
    Récupère un secret depuis AWS Secrets Manager.

    Retour
    ------
    - `str` si le secret a pu être récupéré (SecretString ou SecretBinary décodé).
    - `None` en cas d'erreur (réseau, permissions, secret introuvable, etc.).

    Note
    ----
    Cette fonction n'élève jamais d'exception: c'est le caller qui décide
    de la criticité (mode dégradé vs arrêt explicite).
    """
    try:
        session = (
            boto3.Session(profile_name=profile_name)
            if profile_name
            else boto3.Session()
        )
        client = session.client(
            "secretsmanager", region_name=region_name, config=_BOTO_CONFIG
        )
        response = client.get_secret_value(SecretId=secret_name)

        if "SecretString" in response:
            return response["SecretString"]
        if "SecretBinary" in response:
            return response["SecretBinary"].decode("utf-8")
        return None
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        logger.warning(
            "Secrets Manager get_secret failed: name=%s region=%s code=%s",
            secret_name,
            region_name,
            error_code,
        )
        return None
    except BotoCoreError as e:
        logger.warning(
            "Secrets Manager network error: name=%s region=%s err=%s",
            secret_name,
            region_name,
            e,
        )
        return None
    except Exception as e:  # garde-fou
        logger.warning(
            "Secrets Manager unexpected error: name=%s err=%s", secret_name, e
        )
        return None


def extract_secret_key(secret_value: str, keys: list[str] | None = None) -> str:
    """
    Extrait une clé d'un secret JSON, ou retourne la valeur brute si non-JSON.
    """
    keys = keys or ["api_key", "OPENAI_API_KEY", "key"]
    try:
        secret_dict: dict[str, Any] = json.loads(secret_value)
        for k in keys:
            v = secret_dict.get(k)
            if v:
                return str(v)
    except (json.JSONDecodeError, TypeError):
        pass
    return secret_value
