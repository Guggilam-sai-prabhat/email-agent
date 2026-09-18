"""Gmail message retrieval — read-only. No send/modify/delete methods exist here.

Depends on app.auth.gmail_auth for credentials; never constructs or refreshes
credentials itself.
"""
from __future__ import annotations

from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.auth.gmail_auth import get_credentials


class GmailReadOnlyClient:
    """Thin wrapper around the Gmail API's read-only surface.

    Intentionally exposes only list/get for messages. Do not add send, delete,
    forward, draft, label-modification, archive, or mark-as-read methods here.
    """

    def __init__(self, credentials: Credentials | None = None) -> None:
        self._credentials = credentials or get_credentials()
        self._service = build("gmail", "v1", credentials=self._credentials, cache_discovery=False)

    def list_message_ids(self, query: str = "", max_results: int = 50) -> list[str]:
        response = (
            self._service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        return [m["id"] for m in response.get("messages", [])]

    def get_message(self, message_id: str) -> dict[str, Any]:
        return self._service.users().messages().get(userId="me", id=message_id, format="full").execute()
