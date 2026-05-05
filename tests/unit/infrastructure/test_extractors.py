from __future__ import annotations

from infrastructure.error_collection.extractors import (
    extract_error_info,
    extract_source_artifact,
)


def _raise_inner():
    raise ValueError("explicit boom")


def test_extract_error_info_collects_class_message_file_line_function():
    try:
        _raise_inner()
    except ValueError as e:
        info = extract_error_info(e)

    assert info.class_ == "ValueError"
    assert "explicit boom" in info.message
    assert info.function == "_raise_inner"
    assert info.file is not None and info.file.endswith("test_extractors.py")
    assert info.line is not None and info.line > 0
    assert info.stack and "Traceback" in info.stack


def test_extract_error_info_truncates_message_and_stack():
    huge = "x" * 10_000
    try:
        raise RuntimeError(huge)
    except RuntimeError as e:
        info = extract_error_info(e)
    assert len(info.message) <= 1000
    assert info.stack is not None and len(info.stack) <= 4000


def test_extract_source_artifact_handles_empty():
    art = extract_source_artifact(None, queue_url="https://q")
    assert art.queue_url == "https://q"
    assert art.sqs_message_id is None


def test_extract_source_artifact_redacts_receipt_handle():
    msg = {
        "MessageId": "id-1",
        "ReceiptHandle": "AQEBLongAndSecretReceiptHandleValueXXXXX",
        "Body": "{}",
    }
    art = extract_source_artifact(msg, queue_url="https://q")
    assert art.sqs_message_id == "id-1"
    assert art.receipt_handle_redacted is not None
    assert "redacted" in art.receipt_handle_redacted
    assert "AQEBLong" in art.receipt_handle_redacted
    assert "SecretReceiptHandle" not in art.receipt_handle_redacted


def test_extract_source_artifact_unwraps_ses_inner():
    body = (
        '{"Type":"Notification","Message":'
        '"{\\"mail\\":{\\"messageId\\":\\"ses-1\\",'
        '\\"source\\":\\"u@x.com\\",'
        '\\"commonHeaders\\":{\\"subject\\":\\"Re: vol\\"}}}"}'
    )
    msg = {"MessageId": "id-1", "ReceiptHandle": "AQ", "Body": body}
    art = extract_source_artifact(msg, queue_url="https://q")
    assert art.ses_message_id == "ses-1"
    assert art.sender == "u@x.com"
    assert art.subject == "Re: vol"
    assert art.size_bytes is not None and art.size_bytes > 0


def test_extract_source_artifact_truncates_raw_body():
    msg = {"MessageId": "id", "ReceiptHandle": "AQ", "Body": "x" * 5000}
    art = extract_source_artifact(msg, queue_url="https://q")
    assert art.raw_body_excerpt is not None
    assert len(art.raw_body_excerpt) <= 1024
