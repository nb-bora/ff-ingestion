from __future__ import annotations

from enum import Enum


class ParsingStatus(str, Enum):
    parsed = "parsed"
    parsing_failed = "parsing_failed"
