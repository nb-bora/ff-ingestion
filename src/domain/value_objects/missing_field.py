"""
Value object `MissingField`.

Rôle
----
Décrire un élément manquant ou invalide dans la demande de l'utilisateur, de
façon assez riche pour que le notifier puisse rendre directement une ligne
explicite dans l'email :

    - "Code IATA aéroport de départ (segment 2)"
    - "Format attendu : Code IATA à 3 lettres (ex: CDG)"
    - "Trouvé : (vide)"
    - "Action : Indiquez clairement l'aéroport de départ du second segment."

C'est la **donnée canonique** de la liste à puces des templates
`user.untreatable.*`. Le notifier itère simplement sur `missing_fields[]`.

Où est-ce produit ?
-------------------
- Pour les erreurs de parse côté Ingestion : construit par
  `NotifyFailureUseCase` (limité, on connaît rarement le détail).
- Pour les hard fails Tier 1 : construit par `tier1_resolver.build_missing_fields(...)`
  à partir du catalogue `tier1_catalog`. ff-intelligence-engine produit la même
  structure de son côté pour les events qu'il publie directement.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MissingField:
    """Élément manquant/invalide dans la demande utilisateur."""

    code: str
    path: str
    label: str
    expected: str | None = None
    found: str | None = None
    fix_hint: str | None = None

    def to_dict(self) -> dict:
        """Sérialise en dict prêt à être placé dans `variables.missing_fields[]`."""
        return {
            "code": self.code,
            "path": self.path,
            "label": self.label,
            "expected": self.expected,
            "found": self.found,
            "fix_hint": self.fix_hint,
        }
