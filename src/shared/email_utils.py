"""
Outils de parsing d’emails (RFC822/EML) et extraction de body.

Rôle
----
- Détecter / parser un email brut (headers + body)
- Extraire un texte "propre" pour le parsing OpenAI

Utilisé par
---------
- `presentation.api.ingestion_router.parse_airfare` (parse EML pour extraire sender/subject/threading)
- `infrastructure.parsers.openai_email_parser` (nettoyage du body)

Note
----
Ces utilitaires existent pour reproduire les heuristiques de `ff-ingestion`.
"""

from __future__ import annotations

import quopri
import re
from email import policy
from email.header import decode_header
from email.parser import BytesParser

from logger import logger
from shared.utils import looks_like_raw_email


def _decode_mime_header(value: str | None) -> str | None:
    """
    Decode un header MIME potentiellement encodé (=?utf-8?...?=).

    Utilisé par
    ---------
    - `parse_eml_bytes`
    """
    if not value:
        return value
    parts = decode_header(value)
    out: list[str] = []
    for p, enc in parts:
        if isinstance(p, bytes):
            out.append(p.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(p)
    return "".join(out).strip()


def _extract_text_from_email(msg) -> str:
    """
    Extrait le contenu texte depuis un objet `email.message`.

    Stratégie
    ---------
    - Préfère `text/plain` non-attachment si multipart
    - Sinon, prend le premier `text/*`

    Utilisé par
    ---------
    - `parse_eml_bytes`
    """
    if msg.is_multipart():
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            disp = (part.get("Content-Disposition") or "").lower()
            if ctype == "text/plain" and "attachment" not in disp:
                try:
                    return part.get_content() or ""
                except Exception:
                    payload = part.get_payload(decode=True) or b""
                    return payload.decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )

        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            if ctype.startswith("text/"):
                payload = part.get_payload(decode=True) or b""
                return payload.decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )
        return ""
    try:
        return msg.get_content() or ""
    except Exception:
        payload = msg.get_payload(decode=True) or b""
        return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")


def parse_eml_bytes(eml_bytes: bytes) -> dict:
    """
    Parse un contenu EML (bytes) et retourne un dict normalisé.

    Champs principaux
    -----------------
    - from_email, subject, message_id, in_reply_to, references, reply_to
    - body_text (texte extrait)

    Utilisé par
    ---------
    - `presentation.api.ingestion_router.parse_airfare`
    """
    msg = BytesParser(policy=policy.default).parsebytes(eml_bytes)
    return {
        "message_id": _decode_mime_header(msg.get("Message-ID")),
        "in_reply_to": _decode_mime_header(msg.get("In-Reply-To")),
        "references": _decode_mime_header(msg.get("References")),
        "reply_to": _decode_mime_header(msg.get("Reply-To")),
        "from_email": _decode_mime_header(msg.get("From")),
        "to_email": _decode_mime_header(msg.get("To")),
        "subject": _decode_mime_header(msg.get("Subject")),
        "date_header": _decode_mime_header(msg.get("Date")),
        "body_text": _extract_text_from_email(msg).strip(),
        "raw_eml": eml_bytes.decode("utf-8", errors="replace"),
    }


def _extract_text_plain_part_from_raw_email(raw_text: str) -> str | None:
    """
    Best-effort: extraction manuelle d’une part `text/plain` dans un raw email multipart.

    Utilisé par
    ---------
    - `extract_email_body` en fallback quand `email` parser échoue.
    """
    header_body = re.split(r"\r?\n\r?\n", raw_text, maxsplit=1)
    if len(header_body) != 2:
        return None

    headers_text, _ = header_body
    boundary_match = re.search(
        r'boundary="?([^";\r\n]+)"?', headers_text, flags=re.IGNORECASE
    )
    if not boundary_match:
        return None

    boundary = boundary_match.group(1)
    parts = re.split(rf"--{re.escape(boundary)}(?:--)?\r?\n", raw_text)

    for part in parts:
        if "content-type: text/plain" not in part.lower():
            continue

        part_split = re.split(r"\r?\n\r?\n", part, maxsplit=1)
        if len(part_split) != 2:
            continue

        part_headers, part_body = part_split
        decoded = part_body

        if "content-transfer-encoding: quoted-printable" in part_headers.lower():
            decoded = quopri.decodestring(part_body).decode("utf-8", errors="replace")

        return decoded.strip()
    return None


def extract_email_body(raw_or_plain_text: str) -> str:
    """
    Retourne un body texte "propre" à partir d’un email brut ou texte.

    Utilisé par
    ---------
    - `OpenAIEmailParser` pour limiter le bruit (headers, multiparts)

    Utilise
    -------
    - `shared.utils.looks_like_raw_email`
    - `parse_eml_bytes` / `_extract_text_plain_part_from_raw_email` en fallback
    """
    if not raw_or_plain_text:
        return ""

    text = raw_or_plain_text.strip()
    if looks_like_raw_email(text):
        try:
            parsed = parse_eml_bytes(text.encode("utf-8"))
            parsed_body = (parsed.get("body_text") or "").strip()
            if parsed_body and parsed_body != text:
                return parsed_body
        except Exception as e:
            logger.warning("Failed to extract body from raw email: %s", e)

        manual_body = _extract_text_plain_part_from_raw_email(text)
        if manual_body:
            return manual_body

        parts = re.split(r"\r?\n\r?\n", text, maxsplit=1)
        if len(parts) == 2:
            return parts[1].strip()

    return text
