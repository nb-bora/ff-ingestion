"""
Catalogue canonique des règles Tier 1.

Rôle
----
Source de vérité **partagée** des règles d'éligibilité Tier 1, telles que
définies dans `ff-intelligence-engine/src/rules/tier1_eligibility.py`. Chaque
règle expose :

- `kind`              — `HARD` (rejette l'offre) ou `SOFT` (offre flagguée)
- `severity`          — niveau pour le routage support/notifier
- `paths`             — chemins JSONPath relatifs au `FareEvent` ; `[*]` est
                         résolu dynamiquement par `tier1_resolver`
- `label_fr/label_en` — libellé humain à afficher à l'utilisateur
- `expected`          — format attendu (court, prêt à injecter)
- `fix_hint_fr/en`    — phrase d'action à proposer dans l'email

Pourquoi ici (Domain) ?
----------------------
Cette table est de la **connaissance métier** (qu'est-ce qu'un email valide
côté FairFare ?), pas de l'orchestration ni de l'I/O. Elle reste donc dans
`domain/rules` et n'a aucune dépendance externe.

Contrat partagé
---------------
ff-intelligence-engine peut soit importer ce module (si packaging shared
library), soit dupliquer la table avec les mêmes valeurs textuelles. Les codes
(`FailureCode`) sont la clé d'unicité ; toute modification doit faire l'objet
d'un changement de `SCHEMA_VERSION` dans `notification_event.py` et d'une
mise à jour de `docs/NOTIFICATIONS_CONTRACT.md`.
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.enums.failure_code import FailureCode
from domain.enums.notification import NotificationSeverity


@dataclass(frozen=True)
class RuleSpec:
    """Spécification d'une règle Tier 1 (lecture seule)."""

    kind: str  # "HARD" | "SOFT"
    severity: NotificationSeverity
    paths: tuple[str, ...]
    label_fr: str
    label_en: str
    expected: str | None
    fix_hint_fr: str
    fix_hint_en: str


# ─────────────────────────────────────────────────────────────────────────────
# CATALOGUE — ne jamais retirer une entrée sans bump de schema_version.
# ─────────────────────────────────────────────────────────────────────────────
TIER1_CATALOG: dict[FailureCode, RuleSpec] = {
    FailureCode.T1_R1_INVALID_ITINERARY: RuleSpec(
        kind="HARD",
        severity=NotificationSeverity.error,
        paths=("itineraries",),
        label_fr="Itinéraire manquant ou vide",
        label_en="Missing or empty itinerary",
        expected="Au moins un itinéraire avec des segments",
        fix_hint_fr=(
            "Précisez le ou les itinéraires (vol aller / retour) avec leurs "
            "segments dans votre prochain message."
        ),
        fix_hint_en="Provide at least one itinerary with its segments.",
    ),
    FailureCode.T1_R1_SINGLE_ITINERARY_ROUNDTRIP: RuleSpec(
        kind="SOFT",
        severity=NotificationSeverity.warning,
        paths=("itineraries",),
        label_fr="Aller-retour annoncé mais un seul itinéraire détecté",
        label_en="Round-trip announced but only one itinerary found",
        expected="Deux itinéraires (aller et retour)",
        fix_hint_fr=(
            "Si votre voyage est un aller-retour, indiquez clairement les deux "
            "trajets."
        ),
        fix_hint_en="For a round-trip, list both outbound and return journeys.",
    ),
    FailureCode.T1_R2_SEGMENTS_REQUIRED: RuleSpec(
        kind="HARD",
        severity=NotificationSeverity.error,
        paths=("itineraries[*].segments",),
        label_fr="Segments d'itinéraire absents",
        label_en="Itinerary segments missing",
        expected="Liste non vide des segments (vols) de l'itinéraire",
        fix_hint_fr=(
            "Détaillez chaque vol composant l'itinéraire (départ, arrivée, "
            "horaires)."
        ),
        fix_hint_en=(
            "List every flight composing the itinerary (origin, destination, "
            "times)."
        ),
    ),
    FailureCode.T1_R3_CITY_DATE_REQUIRED: RuleSpec(
        kind="HARD",
        severity=NotificationSeverity.error,
        paths=(
            "itineraries[*].segments[*].departure.iataCode",
            "itineraries[*].segments[*].departure.at",
            "itineraries[*].segments[*].arrival.iataCode",
            "itineraries[*].segments[*].arrival.at",
        ),
        label_fr="Aéroports et dates/heures de chaque segment",
        label_en="Airports and dates/times for each segment",
        expected=(
            "Code IATA à 3 lettres (ex: CDG) et date ISO-8601 "
            "(YYYY-MM-DDTHH:MM)"
        ),
        fix_hint_fr=(
            "Pour chaque vol, précisez le code IATA des aéroports de départ "
            "et d'arrivée ainsi que les dates et heures associées."
        ),
        fix_hint_en=(
            "For every flight, provide the IATA code of departure and arrival "
            "airports as well as the corresponding dates and times."
        ),
    ),
    FailureCode.T1_R4_PRICE_REQUIRED: RuleSpec(
        kind="HARD",
        severity=NotificationSeverity.error,
        paths=("price.total", "price.currency"),
        label_fr="Prix total et devise",
        label_en="Total price and currency",
        expected="Montant numérique et code devise ISO 4217 (ex: EUR)",
        fix_hint_fr=(
            "Indiquez le prix total ainsi que la devise (par exemple "
            "« 412.50 EUR »)."
        ),
        fix_hint_en="State the total amount along with the currency code (e.g. '412.50 EUR').",
    ),
    FailureCode.T1_R5_CABIN_REQUIRED: RuleSpec(
        kind="HARD",
        severity=NotificationSeverity.error,
        paths=("travelerPricings[*].fareDetailsBySegment[*].cabin",),
        label_fr="Classe de cabine pour chaque segment",
        label_en="Cabin class for each segment",
        expected="ECONOMY, PREMIUM_ECONOMY, BUSINESS ou FIRST",
        fix_hint_fr=(
            "Précisez la classe (économique, premium, business, première) "
            "pour chaque segment."
        ),
        fix_hint_en=(
            "Specify the cabin class (economy, premium, business, first) for "
            "every segment."
        ),
    ),
    FailureCode.T1_R6_FULL_NAME_REQUIRED: RuleSpec(
        kind="HARD",
        severity=NotificationSeverity.error,
        paths=("passenger.fullName",),
        label_fr="Nom complet du passager",
        label_en="Passenger full name",
        expected="Prénom et nom du passager principal",
        fix_hint_fr=(
            "Indiquez le nom complet du passager principal (prénom + nom)."
        ),
        fix_hint_en="Provide the full name (first + last) of the main passenger.",
    ),
    FailureCode.T1_R8_SEGMENT_SEAT_AVAILABILITY: RuleSpec(
        kind="HARD",
        severity=NotificationSeverity.error,
        paths=("itineraries[*].segments[*].numberOfBookableSeats",),
        label_fr="Disponibilité des sièges sur chaque segment",
        label_en="Seat availability for each segment",
        expected="Au moins un siège réservable par segment",
        fix_hint_fr=(
            "Vérifiez la disponibilité de chaque vol : aucun segment ne doit "
            "afficher 0 siège disponible."
        ),
        fix_hint_en=(
            "Make sure no segment has zero seats available; provide an offer "
            "with at least one bookable seat."
        ),
    ),
    FailureCode.T1_R8_MISSING_SEAT_DATA: RuleSpec(
        kind="SOFT",
        severity=NotificationSeverity.warning,
        paths=("itineraries[*].segments[*].numberOfBookableSeats",),
        label_fr="Information de disponibilité de sièges absente",
        label_en="Seat availability data missing",
        expected="Champ numberOfBookableSeats renseigné",
        fix_hint_fr=(
            "Si vous avez l'information, indiquez le nombre de sièges encore "
            "disponibles."
        ),
        fix_hint_en="If known, mention the number of bookable seats still available.",
    ),
    FailureCode.T1_R9_MISSING_OPERATING_DETAILS: RuleSpec(
        kind="SOFT",
        severity=NotificationSeverity.warning,
        paths=(
            "itineraries[*].segments[*].aircraft.code",
            "itineraries[*].segments[*].operating.carrierCode",
        ),
        label_fr="Code avion ou compagnie opératrice",
        label_en="Aircraft code or operating carrier",
        expected="Code IATA de l'appareil et de la compagnie opératrice",
        fix_hint_fr=(
            "Si vous l'avez, précisez le type d'avion et la compagnie "
            "opératrice de chaque segment."
        ),
        fix_hint_en=(
            "If known, specify the aircraft type and operating carrier for "
            "each segment."
        ),
    ),
    FailureCode.T1_R10_TICKETING_DATE: RuleSpec(
        kind="SOFT",
        severity=NotificationSeverity.warning,
        paths=("lastTicketingDate",),
        label_fr="Date limite d'émission du billet",
        label_en="Ticketing deadline",
        expected="Date ISO-8601",
        fix_hint_fr=(
            "Indiquez la date limite à laquelle le billet doit être émis."
        ),
        fix_hint_en="Provide the deadline by which the ticket must be issued.",
    ),
}


def get_rule_spec(code: FailureCode) -> RuleSpec | None:
    """Retourne la `RuleSpec` associée à un code Tier 1 (None si non Tier 1)."""
    return TIER1_CATALOG.get(code)
