"""Gmail message retrieval — read-only. No send/modify/delete methods exist here.

Depends on app.auth.gmail_auth for credentials; never constructs or refreshes
credentials itself.
"""
from __future__ import annotations

from typing import Any

import httplib2
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build

from app.auth.gmail_auth import get_credentials
from app.config import settings


class GmailReadOnlyClient:
    """Thin wrapper around the Gmail API's read-only surface.

    Intentionally exposes only list/get for messages and get for threads. Do
    not add send, delete, forward, draft, label-modification, archive, or
    mark-as-read methods here.
    """

    def __init__(self, credentials: Credentials | None = None) -> None:
        self._credentials = credentials or get_credentials()
        # httplib2 has no timeout by default, so a stalled connection would
        # otherwise block the calling thread (a processing run) forever.
        http = AuthorizedHttp(self._credentials, http=httplib2.Http(timeout=settings.gmail_request_timeout_seconds))
        self._service = build("gmail", "v1", http=http, cache_discovery=False)

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

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        return (
            self._service.users()
            .threads()
            .get(userId="me", id=thread_id, format="metadata", metadataHeaders=["Date"])
            .execute()
        )
