from __future__ import annotations

import json
from typing import Any

import boto3
from botocore.exceptions import ClientError


def get_secret(
    *,
    secret_name: str,
    region_name: str,
    profile_name: str | None = None,
) -> str | None:
    """
    Wrapper léger (optionnel) autour de Secrets Manager.
    `config.Settings` gère déjà la résolution; ce module sert si on veut réutiliser.
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
    except ClientError:
        return None


def extract_secret_key(secret_value: str, keys: list[str] | None = None) -> str:
    keys = keys or ["api_key", "OPENAI_API_KEY", "key"]
    try:
        secret_dict: dict[str, Any] = json.loads(secret_value)
        for k in keys:
            v = secret_dict.get(k)
            if v:
                return str(v)
    except Exception:
        pass
    return secret_value
