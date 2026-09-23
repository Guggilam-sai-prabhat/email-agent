from unittest.mock import MagicMock, patch

from app.config import settings
from app.email.gmail_client import GmailReadOnlyClient


def _client_with_mocked_service():
    with patch("app.email.gmail_client.build") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        client = GmailReadOnlyClient(credentials=MagicMock())
    return client, mock_service


def test_client_builds_readonly_gmail_service():
    with patch("app.email.gmail_client.build") as mock_build:
        fake_creds = MagicMock()
        GmailReadOnlyClient(credentials=fake_creds)
        mock_build.assert_called_once()
        args, kwargs = mock_build.call_args
        assert args == ("gmail", "v1")
        assert kwargs["cache_discovery"] is False
        assert "credentials" not in kwargs  # passed via http= instead, so a request timeout can be set


def test_client_applies_a_request_timeout_so_a_stalled_call_cannot_hang_forever():
    fake_creds = MagicMock()
    with patch("app.email.gmail_client.build") as mock_build:
        GmailReadOnlyClient(credentials=fake_creds)
        http = mock_build.call_args.kwargs["http"]
        assert http.http.timeout == settings.gmail_request_timeout_seconds


def test_list_message_ids_returns_ids():
    client, mock_service = _client_with_mocked_service()
    mock_service.users().messages().list().execute.return_value = {
        "messages": [{"id": "msg1"}, {"id": "msg2"}]
    }

    ids = client.list_message_ids(query="is:unread", max_results=10)

    assert ids == ["msg1", "msg2"]


def test_get_message_returns_full_payload():
    client, mock_service = _client_with_mocked_service()
    mock_service.users().messages().get().execute.return_value = {"id": "msg1", "snippet": "hello"}

    message = client.get_message("msg1")

    assert message == {"id": "msg1", "snippet": "hello"}


def test_client_exposes_no_write_methods():
    forbidden = {"send", "delete", "trash", "modify", "batchModify", "batchDelete", "insert", "import_"}
    public_methods = {name for name in dir(GmailReadOnlyClient) if not name.startswith("_")}
    assert public_methods & forbidden == set()
    assert public_methods == {"list_message_ids", "get_message"}


def test_no_real_network_call_is_made():
    """Guard against accidentally hitting a real Gmail account in CI."""
    with patch("app.email.gmail_client.build") as mock_build, patch(
        "app.email.gmail_client.get_credentials"
    ) as mock_get_creds:
        mock_get_creds.return_value = MagicMock()
        GmailReadOnlyClient()
        mock_build.assert_called_once()
        mock_get_creds.assert_called_once()
