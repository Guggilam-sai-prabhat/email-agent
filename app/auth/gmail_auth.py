"""Gmail OAuth authentication — token acquisition and validation only.

Deliberately separate from app/email/gmail_client.py (message retrieval).
This module must never gain send/modify/delete capability: it is the single
place that defines and enforces the allowed OAuth scope.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import structlog
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from app.config import settings

log = structlog.get_logger()

# The only scope this application is allowed to request or accept.
# Read-only: cannot send, delete, modify labels, or draft.
READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
ALLOWED_SCOPES = frozenset({READONLY_SCOPE})

# Scopes that grant write/send capability. If any of these appear on a
# credential, the app must refuse to start (fail closed).
FORBIDDEN_SCOPES = frozenset(
    {
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/gmail.labels",
        "https://www.googleapis.com/auth/gmail.insert",
        "https://www.googleapis.com/auth/gmail.settings.basic",
        "https://www.googleapis.com/auth/gmail.settings.sharing",
        "https://mail.google.com/",
    }
)


class ScopeViolationError(RuntimeError):
    """Raised when a credential carries a scope outside ALLOWED_SCOPES."""


def _token_path() -> Path:
    """Resolve the token file path, defaulting to a location outside the repo."""
    configured = Path(settings.gmail_token_file).expanduser()
    if configured.is_absolute():
        return configured
    default_dir = Path.home() / ".config" / "mail-agent"
    return default_dir / configured.name


def _restrict_permissions(path: Path) -> None:
    """Best-effort: owner-read/write only. No-op where the OS doesn't support it."""
    if os.name != "posix":
        return
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _scopes_stored_on_disk(token_path: Path) -> list[str]:
    """Read the scopes actually recorded in the token file.

    Credentials.from_authorized_user_file echoes back whatever scope list it
    was called with rather than the file's own "scopes" field, which would
    mask a mismatch between what was requested and what was granted. Read
    the file directly instead so scope validation reflects ground truth.
    """
    data = json.loads(token_path.read_text())
    return data.get("scopes", [])


def _validate_scopes(granted_scopes: list[str] | None) -> None:
    """Fail closed if the credential carries any scope outside ALLOWED_SCOPES."""
    granted = set(granted_scopes or [])
    forbidden_present = granted & FORBIDDEN_SCOPES
    if forbidden_present:
        raise ScopeViolationError(
            f"Credential carries {len(forbidden_present)} forbidden write scope(s); refusing to start"
        )
    if not granted.issubset(ALLOWED_SCOPES):
        raise ScopeViolationError("Credential carries unexpected scope(s) outside the allowed set; refusing to start")
    if not granted:
        raise ScopeViolationError("Credential carries no scopes; refusing to start")


def get_credentials() -> Credentials:
    """Load or acquire Gmail OAuth credentials, validating scopes before returning.

    Runs the interactive local-server authorization flow on first use (or when
    no refresh token is available); otherwise loads and refreshes the stored
    token. Never logs tokens, codes, or credential contents.
    """
    token_path = _token_path()
    creds: Credentials | None = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), list(ALLOWED_SCOPES))
        _validate_scopes(_scopes_stored_on_disk(token_path))

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            settings.gmail_credentials_file, list(ALLOWED_SCOPES)
        )
        creds = flow.run_local_server(port=0)
        _validate_scopes(creds.scopes)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())
        _restrict_permissions(token_path)
        log.info("gmail_oauth_token_acquired", path=str(token_path))

    return creds


def has_stored_token() -> bool:
    """Non-interactive check for whether Gmail authorization has been completed."""
    return _token_path().exists()


def validate_stored_token_scopes() -> None:
    """Startup check: if a token is already stored, fail closed on bad scopes.

    Does not trigger the interactive flow — only inspects an existing token,
    so the app can start without Gmail access configured yet.
    """
    token_path = _token_path()
    if not token_path.exists():
        return
    _validate_scopes(_scopes_stored_on_disk(token_path))
