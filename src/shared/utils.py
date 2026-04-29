from __future__ import annotations

import re

_HEADER_LINE_RE = re.compile(r"^[A-Za-z0-9-]+:\s.+$")


def looks_like_raw_email(text: str) -> bool:
    """Heuristique: détecter un bloc d’en-têtes RFC822 avant le body."""
    if not text:
        return False

    header_lines = 0
    for line in text.splitlines()[:40]:
        if not line.strip():
            break
        if _HEADER_LINE_RE.match(line):
            header_lines += 1
            continue
        if line.startswith((" ", "\t")) and header_lines > 0:
            continue
        return False

    return header_lines >= 3
