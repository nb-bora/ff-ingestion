"""
Résolveur Tier 1 → `MissingField[]`.

Rôle
----
Convertir une liste de codes Tier 1 violés (`rule_codes`) en une liste de
`MissingField` riches, prête à être placée dans `NotificationEvent.variables.missing_fields`.

Si un `fare_event` (dict) est fourni :
- Les paths contenant `[*]` sont résolus dynamiquement contre la donnée réelle
  pour cibler **uniquement** les segments/itinéraires fautifs (ex: segment 2
  manquant son code IATA → on ne génère qu'une entrée pour `segments[1]`).

Sinon (cas le plus fréquent côté Ingestion : on n'a pas de fare_event valide à
l'erreur) :
- Les paths sont conservés tels quels (`[*]`) et le notifier affichera un
  texte générique.

Pure : aucun I/O, aucune dépendance AWS, totalement testable.
"""

from __future__ import annotations

import re

from domain.enums.failure_code import FailureCode
from domain.rules.tier1_catalog import TIER1_CATALOG, RuleSpec
from domain.value_objects.missing_field import MissingField

_INDEX_TOKEN = re.compile(r"\[\*\]")


def _split_path(path: str) -> list[str]:
    """Découpe un JSONPath simple en tokens (`a.b[*].c` → ['a', 'b[*]', 'c'])."""
    return [tok for tok in path.split(".") if tok]


def _walk_missing(data, tokens: list[str], cursor: str = "") -> list[str]:
    """
    Parcourt récursivement `data` en suivant `tokens` et retourne la liste des
    paths concrets pour lesquels la valeur est manquante (None / "" / absente).

    - Un token `name[*]` itère sur la liste à la clé `name` et descend.
    - Un token `name` descend dans le dict.
    - Si la donnée n'a pas la forme attendue, on renvoie le path symbolique courant.
    """
    if not tokens:
        if data in (None, ""):
            return [cursor.lstrip(".")]
        return []

    head, *tail = tokens
    if head.endswith("[*]"):
        key = head[:-3]
        if not isinstance(data, dict):
            return [f"{cursor}.{head}".lstrip(".")]
        items = data.get(key)
        if not isinstance(items, list) or not items:
            return [f"{cursor}.{head}".lstrip(".")]
        misses: list[str] = []
        for idx, item in enumerate(items):
            misses.extend(
                _walk_missing(item, tail, f"{cursor}.{key}[{idx}]")
            )
        return misses

    if not isinstance(data, dict):
        return [f"{cursor}.{head}".lstrip(".")]
    return _walk_missing(data.get(head), tail, f"{cursor}.{head}")


def _resolve_path(
    path: str, fare_event: dict | None
) -> list[str]:
    """
    Résout un path symbolique (`itineraries[*].segments[*].departure.at`) en
    paths concrets pour les segments/itinéraires défaillants. Si `fare_event`
    est None, retourne `[path]` tel quel.
    """
    if fare_event is None or not _INDEX_TOKEN.search(path):
        return [path]
    tokens = _split_path(path)
    misses = _walk_missing(fare_event, tokens)
    return misses or [path]


def _missing_field_from_spec(
    *,
    code: FailureCode,
    spec: RuleSpec,
    concrete_path: str,
    fare_event: dict | None,
    locale: str,
) -> MissingField:
    """Compose un `MissingField` à partir d'un path concret et de la spec."""
    label = spec.label_fr if locale == "fr" else spec.label_en
    fix = spec.fix_hint_fr if locale == "fr" else spec.fix_hint_en
    found = _read_value(fare_event, concrete_path) if fare_event is not None else None
    return MissingField(
        code=code.value,
        path=concrete_path,
        label=label,
        expected=spec.expected,
        found=str(found) if found not in (None, "") else None,
        fix_hint=fix,
    )


def _read_value(data: dict | None, path: str):
    """Lecture best-effort d'une valeur via un path concret (sans `[*]`)."""
    if not data:
        return None
    cursor = data
    for raw in _split_path(path):
        if raw.endswith("]") and "[" in raw:
            key, idx = raw[:-1].split("[", 1)
            if not isinstance(cursor, dict):
                return None
            cursor = cursor.get(key)
            try:
                cursor = cursor[int(idx)] if cursor is not None else None
            except (TypeError, ValueError, IndexError):
                return None
        else:
            if not isinstance(cursor, dict):
                return None
            cursor = cursor.get(raw)
        if cursor is None:
            return None
    return cursor


def build_missing_fields(
    rule_codes: list[FailureCode | str],
    fare_event: dict | None = None,
    *,
    locale: str = "fr",
) -> list[MissingField]:
    """
    Matérialise la liste des `MissingField` à partir des règles violées.

    Args
    ----
    - `rule_codes` : codes Tier 1 (FailureCode ou str). Les codes inconnus du
      catalogue sont silencieusement ignorés.
    - `fare_event` : optionnel ; si fourni, les `[*]` sont résolus aux indices
      effectivement défaillants.
    - `locale` : `"fr"` ou `"en"` pour le choix `label`/`fix_hint`.
    """
    out: list[MissingField] = []
    for raw in rule_codes:
        try:
            code = raw if isinstance(raw, FailureCode) else FailureCode(raw)
        except ValueError:
            continue
        spec = TIER1_CATALOG.get(code)
        if spec is None:
            continue
        for symbolic_path in spec.paths:
            for concrete_path in _resolve_path(symbolic_path, fare_event):
                out.append(
                    _missing_field_from_spec(
                        code=code,
                        spec=spec,
                        concrete_path=concrete_path,
                        fare_event=fare_event,
                        locale=locale,
                    )
                )
    return out
