"""
Enums Domain pour les notifications.

Rôle
----
Définir les valeurs canoniques utilisées par `NotificationEvent` :
- `NotificationCategory` distingue le destinataire fonctionnel (utilisateur final
  vs équipe support) et conditionne le contenu du bloc `variables`.
- `NotificationSeverity` aide le notifier (et le support) à prioriser le routage
  (info → digest, critical → page astreinte).
- `NextAction` est une convention de CTA proposée à l'utilisateur dans les
  templates `user_untreatable`.

Aucune dépendance externe : ces enums vivent dans la couche Domain.
"""

from __future__ import annotations

from enum import Enum


class NotificationCategory(str, Enum):
    """Famille fonctionnelle de l'événement de notification."""

    user_untreatable = "user_untreatable"
    support_alert = "support_alert"


class NotificationSeverity(str, Enum):
    """Niveau de gravité utilisé pour le routage côté notifier/support."""

    info = "info"
    warning = "warning"
    error = "error"
    critical = "critical"


class NextAction(str, Enum):
    """
    CTA proposé dans les templates `user_untreatable`.

    Utilisé par le notifier pour piloter le rendu du bouton/lien d'action.
    """

    reply_with_missing_info = "reply_with_missing_info"
    resend_new_quote = "resend_new_quote"
    contact_support = "contact_support"
    no_action = "no_action"
