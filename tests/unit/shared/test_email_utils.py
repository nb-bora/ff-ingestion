from __future__ import annotations

from shared.email_utils import extract_email_body, parse_eml_bytes


def test_parse_eml_bytes_extracts_headers_and_body():
    raw = (
        b"From: Alice <alice@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Subject: Test\r\n"
        b"Message-ID: <msg-1@example.com>\r\n"
        b"\r\n"
        b"Hello world\r\n"
    )
    parsed = parse_eml_bytes(raw)
    assert "alice@example.com" in (parsed["from_email"] or "")
    assert parsed["subject"] == "Test"
    assert parsed["message_id"] == "<msg-1@example.com>"
    assert parsed["body_text"].strip() == "Hello world"


def test_extract_email_body_returns_plain_text_when_no_headers():
    assert extract_email_body("hello") == "hello"
