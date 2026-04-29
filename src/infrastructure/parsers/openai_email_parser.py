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
- `xray_config.subsegment` (trace sur l’appel OpenAI)
- `config.settings` (`openai_api_key`, `openai_model`)

Impact / effets de bord
----------------------
- Appels réseau vers OpenAI (latence/coût).
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

from openai import BadRequestError, OpenAI

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
    """
    Calcule le prochain lundi (strictement après `from_date`).

    Utilisé par
    ---------
    - `_build_system_prompt` (exemples du prompt)
    """
    days_ahead = (0 - from_date.weekday() + 7) % 7
    if days_ahead == 0:
        days_ahead = 7
    return from_date + timedelta(days=days_ahead)


def _build_system_prompt() -> str:
    """
    Rend le prompt système avec placeholders dynamiques (date courante).

    Utilisé par
    ---------
    - `OpenAIEmailParser._parse_with_openai`
    """
    today_dt = date.today()
    return SYSTEM_PROMPT_TEMPLATE.format(
        today=today_dt.strftime("%Y-%m-%d"),
        next_monday=_next_monday(today_dt).strftime("%Y-%m-%d"),
        year=str(today_dt.year),
    )


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
        """
        Initialise le parser et tente de créer le client OpenAI.

        Impact
        ------
        - Peut logger un warning si `OPENAI_API_KEY` absent.
        """
        self._client = self._initialize_openai_client()

    def _initialize_openai_client(self) -> OpenAI | None:
        """
        Initialise `openai.OpenAI` si possible.

        Utilisé par
        ---------
        - `__init__`

        Erreurs
        ------
        - Ne lève pas: retourne `None` et log en cas d’échec.
        """
        if not settings.openai_api_key:
            logger.warning("OpenAI API key non configurée")
            return None
        try:
            return OpenAI(api_key=settings.openai_api_key)
        except Exception as e:
            logger.error("Failed to initialize OpenAI client: %s", e)
            return None

    def _parse_with_openai(self, email: EmailMessage) -> tuple[dict, str | None]:
        """
        Exécute l’appel OpenAI d’extraction (synchrone).

        Utilisé par
        ---------
        - `parse` via `run_in_executor`

        Utilise
        -------
        - `extract_email_body` (nettoyer le body)
        - `SYSTEM_PROMPT_TEMPLATE` (schema JSON)

        Erreurs
        ------
        - `RuntimeError` si OpenAI non configuré.
        """
        if self._client is None:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        body_text = (extract_email_body(email.body_text) or "")[:5000]
        subject = email.subject or ""

        resp = _create_chat_completion_with_fallback(
            client=self._client,
            model=settings.openai_model,
            max_tokens=512,
            messages=[
                {"role": "system", "content": _build_system_prompt()},
                {"role": "user", "content": f"Subject: {subject}\n\n{body_text}"},
            ],
        )

        response_text = resp.choices[0].message.content if resp.choices else ""
        logger.info(
            "OpenAI full response: id=%s model=%s finish_reason=%s content=%s",
            resp.id,
            resp.model,
            resp.choices[0].finish_reason if resp.choices else None,
            response_text,
        )
        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError:
            logger.warning("OpenAI response is not valid JSON: %s", response_text)
            parsed = {}
        return parsed, resp.id

    def _failure_reasons(self, email: EmailMessage) -> tuple[list[str], str | None]:
        """
        Demande à OpenAI des raisons "humaines" expliquant pourquoi la demande
        n’est pas parsable (extraction incomplète).

        Utilisé par
        ---------
        - `parse` quand `origin/destination` manquent ou quand JSON invalide.
        """
        if self._client is None:
            return ["We couldn't process your email at the moment"], None

        failure_prompt = """Analyze why this email is NOT a valid travel request.
Provide 2-3 concise reasons in conversational language.

Input may be English or French. Respond in the same language as the user's message.
If language is unclear, respond in English.
IMPORTANT: Return ONLY a valid JSON object. No explanation, no markdown, no code fences.

Examples:
- "We couldn't find where you want to fly from"
- "We need to know where you're heading"
- "Could you tell us your travel dates?"
- "Nous n'avons pas trouve votre aeroport de depart"
- "Pouvez-vous preciser votre destination?"

Return as JSON: ["reason 1", "reason 2"]"""

        body_text = (extract_email_body(email.body_text) or "")[:3000]
        subject = email.subject or ""

        resp = _create_chat_completion_with_fallback(
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
        logger.info(
            "OpenAI full failure-reasons response: id=%s model=%s finish_reason=%s content=%s",
            resp.id,
            resp.model,
            resp.choices[0].finish_reason if resp.choices else None,
            response_text,
        )
        try:
            reasons = json.loads(response_text)
            if isinstance(reasons, list):
                return [str(r) for r in reasons], resp.id
        except json.JSONDecodeError:
            logger.warning("Failed to parse OpenAI reasons as JSON: %s", response_text)
        return ["We need more details to process your request"], resp.id

    async def parse(
        self, email: EmailMessage
    ) -> tuple[dict, str | None, list[str] | None]:
        """
        Parse async (contrat Application).

        Utilisé par
        ---------
        - `application.use_cases.ParseEmailUseCase.execute`

        Impact
        ------
        - Appel OpenAI dans un threadpool (via executor par défaut).
        """
        if self._client is None:
            logger.warning("OpenAI non configuré")
            return {}, None, ["OpenAI API not configured"]

        loop = asyncio.get_event_loop()
        with subsegment("parse_with_openai"):
            extracted, response_id = await loop.run_in_executor(
                None, self._parse_with_openai, email
            )

        # failure reasons si extraction invalide (align ff-ingestion)
        if not extracted or ("error" in extracted):
            reasons, _ = await loop.run_in_executor(None, self._failure_reasons, email)
            return extracted or {}, response_id, reasons

        origin = extracted.get("origin")
        destination = extracted.get("destination")
        if not origin or not destination:
            reasons, _ = await loop.run_in_executor(None, self._failure_reasons, email)
            return extracted or {}, response_id, reasons

        return extracted, response_id, None


def _create_chat_completion_with_fallback(
    *,
    client: OpenAI,
    model: str,
    messages: list[dict],
    max_tokens: int,
):
    """
    Compatibilité modèles OpenAI.

    Certains modèles (ex: `gpt-5-*`) refusent `max_tokens` et demandent
    `max_completion_tokens`. Pour éviter de casser l’exécution selon le modèle,
    on tente d’abord `max_tokens`, puis on réessaie en fallback.

    Impact
    ------
    - Peut effectuer 2 requêtes OpenAI dans le pire cas (si fallback).
    """
    try:
        return client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )
    except BadRequestError as e:
        msg = str(e)
        if "Unsupported parameter" in msg and "max_tokens" in msg:
            return client.chat.completions.create(
                model=model,
                max_completion_tokens=max_tokens,
                messages=messages,
            )
        raise
