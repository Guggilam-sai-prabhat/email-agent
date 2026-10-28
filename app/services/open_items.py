"""Open-item view: requests that still need a reply, with age and nag_level.

Pure aggregation, same pattern as app.services.daily_brief — takes stored
EmailAnalysis rows plus a precomputed reply-status map (Phase 1's
has_been_replied_to, called by the route) and invents nothing itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from app.config import settings
from app.database.models import EmailAnalysis, ReviewStatus

_CLOSED_REVIEW_STATUSES = {ReviewStatus.COMPLETED, ReviewStatus.DISMISSED}


class NagLevel(str, Enum):
    QUIET = "quiet"
    NOTICEABLE = "noticeable"
    PERSISTENT = "persistent"


@dataclass(frozen=True)
class OpenItem:
    message_id: str
    subject: str
    sender_address: str
    received_at: datetime
    age: timedelta
    nag_level: NagLevel


def _nag_level(age: timedelta) -> NagLevel:
    hours = age.total_seconds() / 3600
    if hours >= settings.nag_persistent_after_hours:
        return NagLevel.PERSISTENT
    if hours >= settings.nag_noticeable_after_hours:
        return NagLevel.NOTICEABLE
    return NagLevel.QUIET


def build_open_items(
    analyses: list[EmailAnalysis],
    *,
    replied_message_ids: set[str],
    now: datetime | None = None,
) -> list[OpenItem]:
    """Open items: category == "request", not already replied to, and not in
    a closed review state (completed/dismissed)."""
    now = now or datetime.now(timezone.utc)
    items = []

    for analysis in analyses:
        if analysis.category != "request":
            continue
        message = analysis.processed_message
        if message.id in replied_message_ids:
            continue
        review_status = analysis.review.review_status if analysis.review else ReviewStatus.PENDING
        if review_status in _CLOSED_REVIEW_STATUSES:
            continue

        age = now - message.received_at
        items.append(
            OpenItem(
                message_id=message.id,
                subject=message.subject,
                sender_address=message.sender_address,
                received_at=message.received_at,
                age=age,
                nag_level=_nag_level(age),
            )
        )

    items.sort(key=lambda item: item.age, reverse=True)
    return items
