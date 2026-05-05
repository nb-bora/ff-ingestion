"""
Parseur OpenAI: transforme un `EmailMessage` en extraction "Travel" structurée.

Rôle
----
- Implémenter `application.interfaces.IEmailParser`.
- Appeler OpenAI Chat Completions avec un prompt strict "JSON only".
- Retourner:
  - `extracted_travel` (dict) et `openai_response_id`
  - et, si extraction incomplète/ambiguë, `failure_reasons` (liste de raisons)

Utilisé par
---------
- `presentation.api.dependencies.get_ingestion_service` (instanciation)
- `application.use_cases.ParseEmailUseCase` (chemins API & consumer)

Utilise
-------
- `shared.email_utils.extract_email_body` (nettoyage corps email)
- `xray_config.subsegment` (trace sur l'appel OpenAI)
- `config.settings` (`get_openai_api_key`, `openai_model`, timeouts, ...)

Robustesse
----------
- Client OpenAI configuré avec `timeout` et `max_retries` (settings).
- `temperature=0` pour une extraction déterministe.
- `response_format={"type": "json_object"}` pour garantir la forme JSON
  (sur les modèles compatibles, cf. `_supports_json_response_format`).
- Fallback `max_completion_tokens` pour les modèles type `gpt-5*` / `o1*`
  basé sur le nom du modèle (pas de string match fragile sur l'erreur).

Logs
----
- Par défaut, on log uniquement les métadonnées de la réponse (id, model,
  finish_reason, length). Le contenu intégral n'est loggé que si
  `LOG_OPENAI_PAYLOAD=true` (DEV uniquement).
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

from openai import OpenAI

from application.interfaces.email_parser import IEmailParser
from config import settings
from domain.entities.email_message import EmailMessage
from logger import logger
from shared.email_utils import extract_email_body
from xray_config import subsegment

SYSTEM_PROMPT_TEMPLATE = """You are a travel request parser. Extract structured flight information from user messages.

Input language can be English or French. Understand both languages, including mixed-language emails.
IMPORTANT: Return ONLY a valid JSON object. No explanation, no markdown, no code fences.

## Output Schema
{{
  "origin": string | null,
  "destination": string | null,
  "trip_type": "one_way" | "round_trip",
  "cabin_class": "economy" | "premium_economy" | "business" | "first",
  "departure_date": string | null,
  "return_date": string | null,
  "passengers": integer,
  "passengers_adults": integer | null,
  "passengers_children": integer | null,
  "missing_fields": string[],
  "confidence": "low" | "medium" | "high"
}}

## Rules
1. IATA codes: Use the primary international airport for cities with multiple options
   (e.g. London -> LHR, New York -> JFK, Tokyo -> NRT) unless the user specifies otherwise.
2. Dates: Today is {today}. Resolve all relative expressions in English or French
   (for example: "next Friday", "in 2 weeks", "vendredi prochain", "dans 2 semaines") to YYYY-MM-DD.
   If only a month is given, use the nearest future date for that month.
3. trip_type: Set "round_trip" if a return date or return intent is mentioned; otherwise "one_way".
4. cabin_class: Default to "economy" if not stated.
5. passengers: Default to 1 if not stated. Set passengers = passengers_adults + passengers_children.
   If breakdown is unknown, set passengers_adults and passengers_children to null.
6. missing_fields: Include only required fields that are absent: "origin", "destination", "departure_date".
   Never include optional fields (return_date, passengers_children, etc.).
7. confidence:
   - "high"  -> origin, destination, and departure_date are all explicitly stated
   - "medium" -> 1-2 key fields are inferred or ambiguous
   - "low"   -> multiple key fields are missing or unclear

## Examples
User: "Flight from Paris to New York next Monday, business class, 2 adults"
{{"origin":"CDG","destination":"JFK","trip_type":"one_way","cabin_class":"business","departure_date":"{next_monday}","return_date":null,"passengers":2,"passengers_adults":2,"passengers_children":null,"missing_fields":[],"confidence":"high"}}

User: "I need to go to London and come back, leaving June 3rd, returning June 10th, me and my 2 kids"
{{"origin":null,"destination":"LHR","trip_type":"round_trip","cabin_class":"economy","departure_date":"{year}-06-03","return_date":"{year}-06-10","passengers":3,"passengers_adults":1,"passengers_children":2,"missing_fields":["origin"],"confidence":"medium"}}

User: "cheapest flight to Barcelona sometime in August"
{{"origin":null,"destination":"BCN","trip_type":"one_way","cabin_class":"economy","departure_date":null,"return_date":null,"passengers":1,"passengers_adults":null,"passengers_children":null,"missing_fields":["origin","departure_date"],"confidence":"low"}}
"""


def _next_monday(from_date: date) -> date:
    """Calcule le prochain lundi (strictement après `from_date`)."""
    days_ahead = (0 - from_date.weekday() + 7) % 7
    if days_ahead == 0:
        days_ahead = 7
    return from_date + timedelta(days=days_ahead)


def _build_system_prompt() -> str:
    """Rend le prompt système avec placeholders dynamiques (date courante)."""
    today_dt = date.today()
    return SYSTEM_PROMPT_TEMPLATE.format(
        today=today_dt.strftime("%Y-%m-%d"),
        next_monday=_next_monday(today_dt).strftime("%Y-%m-%d"),
        year=str(today_dt.year),
    )


def _uses_max_completion_tokens(model: str) -> bool:
    """
    Détermine si le modèle attend `max_completion_tokens` au lieu de `max_tokens`.

    Règle simple basée sur le nom du modèle (pas un string match fragile sur
    le message d'erreur OpenAI).
    """
    m = (model or "").lower()
    return m.startswith(("gpt-5", "o1", "o3", "o4"))


def _supports_json_response_format(model: str) -> bool:
    """
    Détermine si `response_format={"type": "json_object"}` est supporté.

    Disponible depuis `gpt-4-turbo`, `gpt-4o*`, `gpt-3.5-turbo` (>=1106),
    `gpt-5*`, `o1*`, etc. On filtre conservativement les modèles legacy.
    """
    m = (model or "").lower()
    if not m:
        return False
    legacy_prefixes = ("text-davinci", "code-davinci", "gpt-3", "babbage", "curie")
    if any(m.startswith(p) for p in legacy_prefixes):
        return False
    return True


class OpenAIEmailParser(IEmailParser):
    """
    Implémentation OpenAI du contrat `IEmailParser`.

    Convention de retour (parité `ff-ingestion`)
    -------------------------------------------
    - Si OpenAI non configuré: retourne `({}, None, ["OpenAI API not configured"])`
    - Si extraction invalide: retourne `(..., response_id, failure_reasons)`
    - Sinon: retourne `(extracted, response_id, None)`
    """

    def __init__(self):
        """Initialise le parser et tente de créer le client OpenAI."""
        self._client = self._initialize_openai_client()

    def _initialize_openai_client(self) -> OpenAI | None:
        """
        Initialise `openai.OpenAI` si possible, avec timeout et retries.

        Erreurs
        ------
        - Ne lève pas: retourne `None` et log en cas d'échec.
        """
        api_key = settings.get_openai_api_key()
        if not api_key:
            logger.warning("OpenAI API key non configurée")
            return None
        try:
            return OpenAI(
                api_key=api_key,
                timeout=settings.openai_timeout_seconds,
                max_retries=settings.openai_max_retries,
            )
        except Exception as e:
            logger.error("Failed to initialize OpenAI client: %s", e)
            return None

    def _parse_with_openai(self, email: EmailMessage) -> tuple[dict, str | None]:
        """
        Exécute l'appel OpenAI d'extraction (synchrone).

        Erreurs
        ------
        - `RuntimeError` si OpenAI non configuré.
        - Toute exception réseau/auth/quota OpenAI remonte au caller.
        """
        if self._client is None:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        body_text = (extract_email_body(email.body_text) or "")[:5000]
        subject = email.subject or ""

        resp = _create_chat_completion(
            client=self._client,
            model=settings.openai_model,
            max_tokens=512,
            messages=[
                {"role": "system", "content": _build_system_prompt()},
                {"role": "user", "content": f"Subject: {subject}\n\n{body_text}"},
            ],
        )

        response_text = resp.choices[0].message.content if resp.choices else ""
        _log_openai_response("parse", resp, response_text)
        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError:
            logger.warning(
                "OpenAI response is not valid JSON: id=%s len=%d",
                resp.id,
                len(response_text or ""),
            )
            parsed = {}
        return parsed, resp.id

    def _failure_reasons(self, email: EmailMessage) -> tuple[list[str], str | None]:
        """
        Demande à OpenAI des raisons "humaines" expliquant pourquoi la demande
        n'est pas parsable (extraction incomplète).
        """
        if self._client is None:
            return ["We couldn't process your email at the moment"], None

        failure_prompt = """Analyze why this email is NOT a valid travel request.
Provide 2-3 concise reasons in conversational language.

Input may be English or French. Respond in the same language as the user's message.
If language is unclear, respond in English.
IMPORTANT: Return ONLY a valid JSON object with key "reasons" (list of strings).

Examples:
- "We couldn't find where you want to fly from"
- "We need to know where you're heading"
- "Could you tell us your travel dates?"
- "Nous n'avons pas trouve votre aeroport de depart"
- "Pouvez-vous preciser votre destination?"

Return as JSON: {"reasons": ["reason 1", "reason 2"]}"""

        body_text = (extract_email_body(email.body_text) or "")[:3000]
        subject = email.subject or ""

        resp = _create_chat_completion(
            client=self._client,
            model=settings.openai_model,
            max_tokens=256,
            messages=[
                {"role": "system", "content": failure_prompt},
                {
                    "role": "user",
                    "content": f"From: {email.sender}\nSubject: {subject}\n\n{body_text}",
                },
            ],
        )

        response_text = resp.choices[0].message.content if resp.choices else ""
        _log_openai_response("failure_reasons", resp, response_text)
        try:
            payload = json.loads(response_text)
            reasons = payload.get("reasons") if isinstance(payload, dict) else payload
            if isinstance(reasons, list):
                return [str(r) for r in reasons], resp.id
        except json.JSONDecodeError:
            logger.warning(
                "Failed to parse OpenAI reasons as JSON: id=%s len=%d",
                resp.id,
                len(response_text or ""),
            )
        return ["We need more details to process your request"], resp.id

    async def parse(
        self, email: EmailMessage
    ) -> tuple[dict, str | None, list[str] | None]:
        """
        Parse async (contrat Application).

        Impact
        ------
        - Appel OpenAI dans un threadpool (via executor par défaut).
        """
        if self._client is None:
            logger.warning("OpenAI non configuré")
            return {}, None, ["OpenAI API not configured"]

        loop = asyncio.get_running_loop()
        with subsegment("parse_with_openai"):
            extracted, response_id = await loop.run_in_executor(
                None, self._parse_with_openai, email
            )

        if not extracted or ("error" in extracted):
            reasons, _ = await loop.run_in_executor(None, self._failure_reasons, email)
            return extracted or {}, response_id, reasons

        origin = extracted.get("origin")
        destination = extracted.get("destination")
        if not origin or not destination:
            reasons, _ = await loop.run_in_executor(None, self._failure_reasons, email)
            return extracted or {}, response_id, reasons

        return extracted, response_id, None


def _create_chat_completion(
    *,
    client: OpenAI,
    model: str,
    messages: list[dict],
    max_tokens: int,
):
    """
    Wrapper d'appel OpenAI qui:
    - sélectionne `max_tokens` ou `max_completion_tokens` selon le modèle
    - force `temperature=0` (extraction déterministe)
    - active `response_format={"type": "json_object"}` quand supporté
    """
    kwargs: dict = {
        "model": model,
        "messages": messages,
        "temperature": 0,
    }
    if _uses_max_completion_tokens(model):
        kwargs["max_completion_tokens"] = max_tokens
    else:
        kwargs["max_tokens"] = max_tokens
    if _supports_json_response_format(model):
        kwargs["response_format"] = {"type": "json_object"}

    return client.chat.completions.create(**kwargs)


def _log_openai_response(kind: str, resp, response_text: str | None) -> None:
    """
    Log la réponse OpenAI sans contenu en prod (PII potentielles).
    Le contenu intégral n'est loggé que si `LOG_OPENAI_PAYLOAD=true`.
    """
    finish = resp.choices[0].finish_reason if resp.choices else None
    base = (
        "OpenAI %s response: id=%s model=%s finish_reason=%s len=%d",
        kind,
        getattr(resp, "id", None),
        getattr(resp, "model", None),
        finish,
        len(response_text or ""),
    )
    logger.info(*base)

    if settings.log_openai_payload:
        logger.debug(
            "OpenAI %s full payload: id=%s content=%s",
            kind,
            getattr(resp, "id", None),
            response_text,
        )
