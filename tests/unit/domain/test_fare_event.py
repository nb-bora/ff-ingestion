from __future__ import annotations

from domain.entities.fare_event import FareEvent, _build_fare_event_id
from domain.enums.parsing_status import ParsingStatus
from domain.value_objects.email_metadata import EmailThreadMetadata


def test_build_fare_event_id_deterministic_with_message_id():
    a = _build_fare_event_id(sender="a@x.com", thread_message_id="<abc@x>")
    b = _build_fare_event_id(sender="a@x.com", thread_message_id="<abc@x>")
    assert a == b


def test_build_fare_event_id_random_without_message_id():
    a = _build_fare_event_id(sender="a@x.com", thread_message_id=None)
    b = _build_fare_event_id(sender="a@x.com", thread_message_id=None)
    assert a != b


def test_fare_event_create_uses_uuid5_when_thread_message_id_present():
    thread = EmailThreadMetadata(message_id="<msg-1@x>")
    fe1 = FareEvent.create(
        sender="a@x.com",
        email_body_length=10,
        status=ParsingStatus.parsed,
        thread=thread,
    )
    fe2 = FareEvent.create(
        sender="a@x.com",
        email_body_length=20,  # body length différent → ne change pas l'id
        status=ParsingStatus.parsed,
        thread=thread,
    )
    assert fe1.id == fe2.id


def test_fare_event_create_normalizes_status_enum():
    fe = FareEvent.create(
        sender="a@x.com",
        email_body_length=0,
        status=ParsingStatus.parsing_failed,
    )
    assert fe.status == "parsing_failed"


def test_fare_event_to_dict_contains_all_fields():
    fe = FareEvent.create(
        sender="a@x.com",
        email_body_length=42,
        status=ParsingStatus.parsed,
        subject="hello",
        extracted_travel={"origin": "CDG"},
    )
    d = fe.to_dict()
    assert d["sender"] == "a@x.com"
    assert d["email_body_length"] == 42
    assert d["status"] == "parsed"
    assert d["subject"] == "hello"
    assert d["extracted_travel"] == {"origin": "CDG"}
    assert "id" in d
    assert "parsed_at" in d
