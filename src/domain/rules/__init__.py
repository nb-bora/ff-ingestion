"""
Module Domain.rules — connaissance métier des règles d'éligibilité.

Exporte le catalogue Tier 1 (source de vérité) et le résolveur de
`MissingField`. Aucun I/O, aucune dépendance externe.
"""

from domain.rules.tier1_catalog import TIER1_CATALOG, RuleSpec, get_rule_spec
from domain.rules.tier1_resolver import build_missing_fields

__all__ = [
    "TIER1_CATALOG",
    "RuleSpec",
    "get_rule_spec",
    "build_missing_fields",
]
