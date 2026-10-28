from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from app.config import settings
from app.email.retrieval import EmailDocument, GmailRetrievalService
from tests.fixtures.gmail_payloads import (
    AUTOMATED_MESSAGE,
    EMPTY_BODY_MESSAGE,
    HTML_ONLY_MESSAGE,
    MESSAGE_WITH_ATTACHMENT,
    MISSING_HEADERS_MESSAGE,
    MULTIPART_MESSAGE,
    PLAIN_TEXT_MESSAGE,
)


def _service_with_mock_client():
    mock_client = MagicMock()
    return GmailRetrievalService(client=mock_client), mock_client


def _http_error(status: int) -> HttpError:
    resp = MagicMock(status=status)
    return HttpError(resp=resp, content=b"{}")


def test_list_candidate_message_ids_scopes_query_to_label_and_age(monkeypatch):
    monkeypatch.setattr(settings, "gmail_allowed_label", "Agent-Summary")
    service, mock_client = _service_with_mock_client()
    mock_client.list_message_ids.return_value = ["fake-msg-1"]

    ids = service.list_candidate_message_ids()

    assert ids == ["fake-msg-1"]
    _, kwargs = mock_client.list_message_ids.call_args
    assert 'label:"Agent-Summary"' in kwargs["query"]
    assert "after:" in kwargs["query"]
    assert kwargs["max_results"] == 50


def test_list_candidate_message_ids_falls_back_to_recent_inbox_without_a_label(monkeypatch):
    """With no label configured, the agent should not require the operator to
    create and apply a Gmail label before it can find any messages — it falls
    back to the most recent inbox messages, capped by
    gmail_unlabeled_message_limit rather than the general per-run cap.
    """
    monkeypatch.setattr(settings, "gmail_allowed_label", "")
    monkeypatch.setattr(settings, "gmail_unlabeled_message_limit", 10)
    service, mock_client = _service_with_mock_client()
    mock_client.list_message_ids.return_value = ["fake-msg-1"]

    ids = service.list_candidate_message_ids()

    assert ids == ["fake-msg-1"]
    _, kwargs = mock_client.list_message_ids.call_args
    assert "label:" not in kwargs["query"]
    assert "in:inbox" in kwargs["query"]
    assert "after:" in kwargs["query"]
    assert kwargs["max_results"] == 10


def test_get_message_for_analysis_returns_email_document():
    service, mock_client = _service_with_mock_client()
    mock_client.get_message.return_value = PLAIN_TEXT_MESSAGE

    doc = service.get_message_for_analysis("fake-msg-1")

    assert isinstance(doc, EmailDocument)
    assert doc.message_id == "fake-msg-1"
    assert doc.thread_id == "fake-thread-1"
    assert doc.sender == "Sender Example <sender@example.test>"
    assert doc.recipients == ["recipient@example.test", "second@example.test"]
    assert doc.subject == "Quarterly update"
    assert doc.date is not None
    assert doc.body == "Hello team, here is the update."


def test_multipart_message_prefers_plain_text_part():
    service, mock_client = _service_with_mock_client()
    mock_client.get_message.return_value = MULTIPART_MESSAGE

    doc = service.get_message_for_analysis("fake-msg-2")

    assert doc.body == "Plain text body."
    assert "<html>" not in doc.body


def test_html_only_message_is_stripped_to_text_with_no_script_content():
    service, mock_client = _service_with_mock_client()
    mock_client.get_message.return_value = HTML_ONLY_MESSAGE

    doc = service.get_message_for_analysis("fake-msg-3")

    assert "Hello" in doc.body
    assert "world" in doc.body
    assert "evil" not in doc.body
    assert "<" not in doc.body


def test_empty_body_message_yields_empty_string():
    service, mock_client = _service_with_mock_client()
    mock_client.get_message.return_value = EMPTY_BODY_MESSAGE

    doc = service.get_message_for_analysis("fake-msg-4")

    assert doc.body == ""


def test_missing_headers_default_to_empty_strings():
    service, mock_client = _service_with_mock_client()
    mock_client.get_message.return_value = MISSING_HEADERS_MESSAGE

    doc = service.get_message_for_analysis("fake-msg-5")

    assert doc.sender == ""
    assert doc.recipients == []
    assert doc.subject == ""
    assert doc.date is None
    assert doc.body == "No headers here."


def test_attachment_parts_are_never_included_in_body():
    service, mock_client = _service_with_mock_client()
    mock_client.get_message.return_value = MESSAGE_WITH_ATTACHMENT

    doc = service.get_message_for_analysis("fake-msg-6")

    assert doc.body == "Body text."
    assert "attachment" not in doc.body.lower()


def test_automated_message_is_retrieved_like_any_other():
    service, mock_client = _service_with_mock_client()
    mock_client.get_message.return_value = AUTOMATED_MESSAGE

    doc = service.get_message_for_analysis("fake-msg-7")

    assert doc.sender == "no-reply@example.test"
    assert doc.body == "This is an automated message."


def test_long_body_is_truncated_to_configured_max(monkeypatch):
    monkeypatch.setattr("app.email.retrieval.settings.email_max_body_characters", 10)
    service, mock_client = _service_with_mock_client()
    mock_client.get_message.return_value = PLAIN_TEXT_MESSAGE

    doc = service.get_message_for_analysis("fake-msg-1")

    assert len(doc.body) == 10
    assert doc.truncated is True


def test_get_message_retries_on_rate_limit_then_succeeds(monkeypatch):
    monkeypatch.setattr("app.email.retrieval.time.sleep", lambda *_: None)
    service, mock_client = _service_with_mock_client()
    mock_client.get_message.side_effect = [_http_error(429), PLAIN_TEXT_MESSAGE]

    doc = service.get_message_for_analysis("fake-msg-1")

    assert doc.message_id == "fake-msg-1"
    assert mock_client.get_message.call_count == 2


def test_get_message_gives_up_after_persistent_error(monkeypatch):
    monkeypatch.setattr("app.email.retrieval.time.sleep", lambda *_: None)
    service, mock_client = _service_with_mock_client()
    mock_client.get_message.side_effect = _http_error(503)

    doc = service.get_message_for_analysis("fake-msg-1")

    assert doc is None
    assert mock_client.get_message.call_count == 4  # 1 + _MAX_RETRIES


def test_get_message_does_not_retry_non_retryable_error(monkeypatch):
    monkeypatch.setattr("app.email.retrieval.time.sleep", lambda *_: None)
    service, mock_client = _service_with_mock_client()
    mock_client.get_message.side_effect = _http_error(404)

    doc = service.get_message_for_analysis("fake-msg-1")

    assert doc is None
    assert mock_client.get_message.call_count == 1


def test_list_candidate_ids_returns_empty_list_on_persistent_error(monkeypatch):
    monkeypatch.setattr("app.email.retrieval.time.sleep", lambda *_: None)
    service, mock_client = _service_with_mock_client()
    mock_client.list_message_ids.side_effect = _http_error(500)

    ids = service.list_candidate_message_ids()

    assert ids == []


def test_service_exposes_only_the_narrow_interface():
    public_methods = {
        name for name in dir(GmailRetrievalService) if not name.startswith("_")
    }
    assert public_methods == {
        "list_candidate_message_ids",
        "get_message_for_analysis",
        "has_been_replied_to",
    }


def test_service_does_not_expose_underlying_gmail_service():
    service, mock_client = _service_with_mock_client()
    assert not hasattr(service, "service")
    assert not hasattr(service, "_service")


@pytest.mark.parametrize(
    "attr",
    ["send", "modify", "delete", "trash", "batchModify", "batchDelete"],
)
def test_no_mutating_methods_exist_on_retrieval_service(attr):
    assert not hasattr(GmailRetrievalService, attr)


def _thread_message(label_ids, date_header):
    return {
        "labelIds": label_ids,
        "payload": {"headers": [{"name": "Date", "value": date_header}]},
    }


def test_has_been_replied_to_true_when_sent_message_is_after_cutoff():
    service, mock_client = _service_with_mock_client()
    mock_client.get_thread.return_value = {
        "messages": [
            _thread_message(["INBOX"], "Mon, 1 Jan 2024 10:00:00 +0000"),
            _thread_message(["SENT"], "Mon, 1 Jan 2024 12:00:00 +0000"),
        ]
    }
    cutoff = datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc)

    assert service.has_been_replied_to("fake-thread-1", after=cutoff) is True
    mock_client.get_thread.assert_called_once_with("fake-thread-1")


def test_has_been_replied_to_false_when_sent_message_is_before_cutoff():
    service, mock_client = _service_with_mock_client()
    mock_client.get_thread.return_value = {
        "messages": [_thread_message(["SENT"], "Mon, 1 Jan 2024 09:00:00 +0000")]
    }
    cutoff = datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc)

    assert service.has_been_replied_to("fake-thread-1", after=cutoff) is False


def test_has_been_replied_to_false_with_no_sent_messages():
    service, mock_client = _service_with_mock_client()
    mock_client.get_thread.return_value = {
        "messages": [_thread_message(["INBOX"], "Mon, 1 Jan 2024 12:00:00 +0000")]
    }

    assert service.has_been_replied_to("fake-thread-1", after=datetime(2024, 1, 1, tzinfo=timezone.utc)) is False


def test_has_been_replied_to_false_on_persistent_error(monkeypatch):
    monkeypatch.setattr("app.email.retrieval.time.sleep", lambda _: None)
    service, mock_client = _service_with_mock_client()
    mock_client.get_thread.side_effect = _http_error(500)

    assert service.has_been_replied_to("fake-thread-1", after=datetime(2024, 1, 1, tzinfo=timezone.utc)) is False
