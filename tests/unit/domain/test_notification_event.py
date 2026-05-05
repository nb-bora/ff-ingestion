from __future__ import annotations

from domain.entities.notification_event import NotificationEvent
from domain.enums.failure_code import FailureCode
from domain.enums.notification import (
    NextAction,
    NotificationCategory,
    NotificationSeverity,
)
from domain.value_objects.error_info import ErrorInfo
from domain.value_objects.missing_field import MissingField


def test_make_user_untreatable_full_payload():
    mf = MissingField(
        code="T1_R3_CITY_DATE_REQUIRED",
        path="itineraries[0].segments[1].departure.iataCode",
        label="Code IATA aéroport de départ (segment 2)",
        expected="Code IATA à 3 lettres (ex: CDG)",
        found=None,
        fix_hint="Indiquez clairement l'aéroport de départ.",
    )
    event = NotificationEvent.make_user_untreatable(
        service="ff-ingestion",
        environment="prod",
        failure_code=FailureCode.T1_R3_CITY_DATE_REQUIRED,
        template_id="user.untreatable.tier1_hard",
        sender="user@example.com",
        missing_fields=[mf],
        blocking_rules=["T1_R3_CITY_DATE_REQUIRED"],
        non_blocking_rules=["T1_R10_TICKETING_DATE"],
        signals=["MISSING_TICKETING_DATE"],
        original_subject="Re: vol",
        original_snippet="Bonjour, voici mon billet ...",
        human_summary="Code IATA et date manquants.",
        next_action=NextAction.reply_with_missing_info,
        support_contact="support@fairfare.example",
        source_message_id="<abc@mail.gmail.com>",
    )

    payload = event.to_dict()
    assert payload["category"] == "user_untreatable"
    assert payload["recipient"]["type"] == "user"
    assert payload["recipient"]["email"] == "user@example.com"
    assert payload["variables"]["missing_fields"][0]["code"] == "T1_R3_CITY_DATE_REQUIRED"
    assert payload["variables"]["blocking_rules"] == ["T1_R3_CITY_DATE_REQUIRED"]
    assert payload["variables"]["next_action"] == "reply_with_missing_info"
    assert payload["variables"]["support_contact"] == "support@fairfare.example"


def test_event_id_deterministic_on_same_inputs():
    common = dict(
        service="ff-ingestion",
        environment="prod",
        failure_code=FailureCode.PARSE_FAILED,
        template_id="user.untreatable.parse_failed",
        sender="user@example.com",
        source_message_id="<same-id@example.com>",
    )
    a = NotificationEvent.make_user_untreatable(**common)
    b = NotificationEvent.make_user_untreatable(**common)
    assert a.event_id == b.event_id


def test_event_id_random_when_no_source_message_id():
    common = dict(
        service="ff-ingestion",
        environment="prod",
        failure_code=FailureCode.PARSE_FAILED,
        template_id="user.untreatable.parse_failed",
        sender="user@example.com",
    )
    a = NotificationEvent.make_user_untreatable(**common)
    b = NotificationEvent.make_user_untreatable(**common)
    assert a.event_id != b.event_id


def test_make_support_alert_with_error_info():
    err = ErrorInfo(
        class_="ParseError",
        message="OpenAI returned non-JSON",
        module="infrastructure.parsers.openai_email_parser",
        file="src/infrastructure/parsers/openai_email_parser.py",
        line=187,
        function="_parse_with_retries",
        stack="Traceback ...",
    )
    event = NotificationEvent.make_support_alert(
        service="ff-ingestion",
        environment="prod",
        failure_code=FailureCode.POISON_MESSAGE,
        template_id="support.poison_message",
        error=err,
        receive_count=4,
        host="ff-ingestion-pod-7c9",
        deploy_sha="9f3c1ab",
    )
    payload = event.to_dict()
    assert payload["category"] == "support_alert"
    assert payload["recipient"]["email"] is None
    assert payload["variables"]["error"]["class"] == "ParseError"
    assert payload["variables"]["error"]["line"] == 187
    assert payload["variables"]["occurrence"]["receive_count"] == 4


def test_severity_default_is_warning_for_user_and_error_for_support():
    user = NotificationEvent.make_user_untreatable(
        service="ff-ingestion",
        environment="prod",
        failure_code=FailureCode.PARSE_FAILED,
        template_id="user.untreatable.parse_failed",
        sender="u@x.com",
    )
    support = NotificationEvent.make_support_alert(
        service="ff-ingestion",
        environment="prod",
        failure_code=FailureCode.POISON_MESSAGE,
        template_id="support.poison_message",
        error=ErrorInfo(class_="X", message="m"),
    )
    assert user.severity == NotificationSeverity.warning
    assert support.severity == NotificationSeverity.error
    assert user.category == NotificationCategory.user_untreatable
    assert support.category == NotificationCategory.support_alert
