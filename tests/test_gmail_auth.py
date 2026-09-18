import json
import stat
from unittest.mock import MagicMock, patch

import pytest

from app.auth.gmail_auth import (
    ALLOWED_SCOPES,
    READONLY_SCOPE,
    ScopeViolationError,
    _restrict_permissions,
    _token_path,
    _validate_scopes,
    get_credentials,
    validate_stored_token_scopes,
)


def test_only_readonly_scope_is_allowed():
    assert ALLOWED_SCOPES == {READONLY_SCOPE}
    assert READONLY_SCOPE == "https://www.googleapis.com/auth/gmail.readonly"


def test_validate_scopes_accepts_readonly():
    _validate_scopes([READONLY_SCOPE])  # must not raise


@pytest.mark.parametrize(
    "scopes",
    [
        ["https://www.googleapis.com/auth/gmail.modify"],
        ["https://www.googleapis.com/auth/gmail.send"],
        ["https://www.googleapis.com/auth/gmail.compose"],
        ["https://mail.google.com/"],
        [READONLY_SCOPE, "https://www.googleapis.com/auth/gmail.modify"],
    ],
)
def test_validate_scopes_rejects_write_scopes(scopes):
    with pytest.raises(ScopeViolationError):
        _validate_scopes(scopes)


def test_validate_scopes_rejects_unknown_scope():
    with pytest.raises(ScopeViolationError):
        _validate_scopes(["https://www.googleapis.com/auth/some.other.scope"])


def test_validate_scopes_rejects_empty():
    with pytest.raises(ScopeViolationError):
        _validate_scopes([])


def test_token_path_defaults_outside_repo(monkeypatch):
    monkeypatch.setattr("app.auth.gmail_auth.settings.gmail_token_file", "token.json")
    path = _token_path()
    assert str(path).startswith(str(__import__("pathlib").Path.home()))
    assert "email-agent" not in str(path)


def test_token_path_respects_absolute_override(monkeypatch, tmp_path):
    override = tmp_path / "custom_token.json"
    monkeypatch.setattr("app.auth.gmail_auth.settings.gmail_token_file", str(override))
    assert _token_path() == override


def test_restrict_permissions_sets_owner_only(tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text("{}")
    _restrict_permissions(token_file)
    mode = stat.S_IMODE(token_file.stat().st_mode)
    assert mode == stat.S_IRUSR | stat.S_IWUSR


def test_validate_stored_token_scopes_noop_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.gmail_auth._token_path", lambda: tmp_path / "missing.json")
    validate_stored_token_scopes()  # must not raise


def test_validate_stored_token_scopes_fails_closed_on_write_scope(monkeypatch, tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text(
        json.dumps(
            {
                "token": "fake-access-token",
                "refresh_token": "fake-refresh-token",
                "client_id": "fake-client-id",
                "client_secret": "fake-client-secret",
                "scopes": [READONLY_SCOPE, "https://www.googleapis.com/auth/gmail.modify"],
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )
    )
    monkeypatch.setattr("app.auth.gmail_auth._token_path", lambda: token_file)

    with pytest.raises(ScopeViolationError):
        validate_stored_token_scopes()


def test_get_credentials_runs_interactive_flow_when_no_token(monkeypatch, tmp_path, capsys):
    token_file = tmp_path / "token.json"
    monkeypatch.setattr("app.auth.gmail_auth._token_path", lambda: token_file)

    fake_creds = MagicMock()
    fake_creds.valid = True
    fake_creds.expired = False
    fake_creds.scopes = [READONLY_SCOPE]
    fake_creds.to_json.return_value = json.dumps({"token": "fake-access-token", "scopes": [READONLY_SCOPE]})

    with patch("app.auth.gmail_auth.InstalledAppFlow") as mock_flow_cls:
        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = fake_creds
        mock_flow_cls.from_client_secrets_file.return_value = mock_flow

        creds = get_credentials()

    assert creds is fake_creds
    mock_flow_cls.from_client_secrets_file.assert_called_once()
    called_scopes = mock_flow_cls.from_client_secrets_file.call_args.args[1]
    assert set(called_scopes) == ALLOWED_SCOPES
    assert token_file.exists()

    # No secret material should have reached stdout/stderr.
    captured = capsys.readouterr()
    assert "fake-access-token" not in captured.out
    assert "fake-access-token" not in captured.err


def test_get_credentials_rejects_flow_result_with_write_scope(monkeypatch, tmp_path):
    token_file = tmp_path / "token.json"
    monkeypatch.setattr("app.auth.gmail_auth._token_path", lambda: token_file)

    fake_creds = MagicMock()
    fake_creds.valid = True
    fake_creds.expired = False
    fake_creds.scopes = [READONLY_SCOPE, "https://mail.google.com/"]
    fake_creds.to_json.return_value = "{}"

    with patch("app.auth.gmail_auth.InstalledAppFlow") as mock_flow_cls:
        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = fake_creds
        mock_flow_cls.from_client_secrets_file.return_value = mock_flow

        with pytest.raises(ScopeViolationError):
            get_credentials()
