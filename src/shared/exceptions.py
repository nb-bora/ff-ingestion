from __future__ import annotations


class IngestionError(Exception):
    """Erreur générique du service d’ingestion."""


class ConfigurationError(IngestionError):
    """Configuration invalide ou incomplète."""


class ParseError(IngestionError):
    """Erreur lors du parsing / extraction."""


class MissingSenderError(ParseError):
    """Impossible d’identifier l’expéditeur; aucun retour downstream possible."""
