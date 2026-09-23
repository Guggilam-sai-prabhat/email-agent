"""Read-only Gmail retrieval — the only surface the rest of the app may use to read mail.

Wraps GmailReadOnlyClient (raw Gmail API access) with the actual retrieval
policy: label scope, age limit, per-run cap, and MIME parsing into
EmailDocument. Never exposes the underlying Gmail service or raw API
payloads to callers.
"""
from __future__ import annotations

import base64
import binascii
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import structlog
from googleapiclient.errors import HttpError

from app.config import settings
from app.email.gmail_client import GmailReadOnlyClient
from app.email.sanitization import SanitizedEmail, sanitize_email

log = structlog.get_logger()

# HTTP statuses worth retrying: rate limit and transient server errors.
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_BASE_BACKOFF_SECONDS = 1.0


@dataclass(frozen=True)
class EmailDocument:
    """Internal DTO for a message handed to the analysis pipeline. Never the raw Gmail payload."""

    message_id: str
    thread_id: str
    sender: str
    recipients: list[str]
    subject: str
    date: datetime | None
    body: str
    truncated: bool = False
    quoted_history: str = ""
    content_hash: str = ""

    def render_for_analysis(self) -> str:
        """Render headers + sanitized body as an opaque, delimited user-data
        block. This is the only representation of the email that may be
        passed to an LLM, and only as user data — never interpolated into a
        system or developer prompt."""
        headers_text = (
            f"From: {self.sender}\n"
            f"To: {', '.join(self.recipients)}\n"
            f"Subject: {self.subject}\n"
            f"Date: {self.date.isoformat() if self.date else ''}"
        )
        return SanitizedEmail(
            headers_text=headers_text,
            current_message=self.body,
            quoted_history=self.quoted_history,
            truncated=self.truncated,
            content_hash=self.content_hash,
        ).render()


def _decode_body_data(data: str | None) -> str:
    if not data:
        return ""
    try:
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (binascii.Error, ValueError):
        return ""
    return raw.decode("utf-8", errors="replace")


def _walk_parts(part: dict[str, Any]) -> list[dict[str, Any]]:
    parts = part.get("parts")
    if not parts:
        return [part]
    flattened = []
    for sub in parts:
        flattened.extend(_walk_parts(sub))
    return flattened


def _extract_body_parts(payload: dict[str, Any]) -> tuple[str, str]:
    """Pick raw plain/html text from a message payload. Ignores attachments
    (any part with a filename) and inline images — those are never fetched
    or included. Returns (plain_text, html_text); either may be empty.
    """
    plain_chunks: list[str] = []
    html_chunks: list[str] = []

    for part in _walk_parts(payload):
        if part.get("filename"):
            continue  # attachment — never retrieved
        mime_type = part.get("mimeType", "")
        body_data = part.get("body", {}).get("data")
        if mime_type == "text/plain" and body_data:
            plain_chunks.append(_decode_body_data(body_data))
        elif mime_type == "text/html" and body_data:
            html_chunks.append(_decode_body_data(body_data))

    return "\n".join(plain_chunks), "\n".join(html_chunks)


def _header(headers: list[dict[str, str]], name: str) -> str:
    for header in headers:
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""


def _parse_recipients(raw: str) -> list[str]:
    if not raw:
        return []
    return [addr.strip() for addr in raw.split(",") if addr.strip()]


def _parse_date(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _to_email_document(raw_message: dict[str, Any], max_body_characters: int) -> EmailDocument:
    payload = raw_message.get("payload", {}) or {}
    headers = payload.get("headers", []) or []

    sender = _header(headers, "From")
    recipients = _parse_recipients(_header(headers, "To"))
    subject = _header(headers, "Subject")
    date = _parse_date(_header(headers, "Date"))
    headers_text = f"From: {sender}\nTo: {', '.join(recipients)}\nSubject: {subject}\nDate: {date.isoformat() if date else ''}"

    plain_body, html_body = _extract_body_parts(payload)
    sanitized = sanitize_email(
        headers_text=headers_text,
        plain_body=plain_body,
        html_body=html_body,
        max_body_characters=max_body_characters,
    )

    return EmailDocument(
        message_id=raw_message.get("id", ""),
        thread_id=raw_message.get("threadId", ""),
        sender=sender,
        recipients=recipients,
        subject=subject,
        date=date,
        body=sanitized.current_message,
        truncated=sanitized.truncated,
        quoted_history=sanitized.quoted_history,
        content_hash=sanitized.content_hash,
    )


def _with_retry(operation, *, log_event: str):
    """Retry an HttpError-raising Gmail call on rate-limit/transient statuses only."""
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return operation()
        except HttpError as exc:
            status = getattr(exc, "status_code", None) or getattr(getattr(exc, "resp", None), "status", None)
            if status not in _RETRYABLE_STATUSES or attempt == _MAX_RETRIES:
                raise
            delay = _BASE_BACKOFF_SECONDS * (2**attempt) + random.uniform(0, 0.5)
            log.warning(log_event, attempt=attempt + 1, status=status, delay=delay)
            time.sleep(delay)


class MessageUnavailableError(RuntimeError):
    """Raised when a message can't be retrieved (deleted, inaccessible, or persistent API error)."""


class GmailRetrievalService:
    """Narrow, read-only application interface onto Gmail.

    This is the only class the rest of the application should import to read
    mail. It never exposes the Gmail service object, raw API dicts, or
    write-capable methods.
    """

    def __init__(self, client: GmailReadOnlyClient | None = None) -> None:
        self._client = client or GmailReadOnlyClient()

    def list_candidate_message_ids(self) -> list[str]:
        """IDs of messages eligible for analysis: recent, capped per run, and
        filtered to gmail_allowed_label when one is configured. With no label
        configured, falls back to the most recent inbox messages instead of
        requiring the operator to create and apply a label first.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.email_max_age_days)
        query = f"after:{int(cutoff.timestamp())}"
        max_results = settings.email_max_messages_per_run
        if settings.gmail_allowed_label:
            query = f'label:"{settings.gmail_allowed_label}" {query}'
        else:
            query = f"in:inbox {query}"
            max_results = settings.gmail_unlabeled_message_limit
        try:
            return _with_retry(
                lambda: self._client.list_message_ids(query=query, max_results=max_results),
                log_event="gmail_list_retry",
            )
        except HttpError as exc:
            log.error("gmail_list_failed", status=getattr(exc, "status_code", None))
            return []

    def get_message_for_analysis(self, message_id: str) -> EmailDocument | None:
        """Fetch and parse one message. Returns None if it's unavailable after retries."""
        try:
            raw_message = _with_retry(
                lambda: self._client.get_message(message_id),
                log_event="gmail_get_retry",
            )
        except HttpError as exc:
            log.error("gmail_get_failed", message_id=message_id, status=getattr(exc, "status_code", None))
            return None
        return _to_email_document(raw_message, settings.email_max_body_characters)
