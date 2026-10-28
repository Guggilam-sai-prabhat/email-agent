import hashlib
from datetime import datetime, timedelta, timezone

from app.database.models import EmailAnalysis, EmailReview, ProcessedMessage, ReviewStatus
from app.services.open_items import NagLevel, build_open_items

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def make_analysis(
    message_id: str,
    subject: str,
    *,
    category: str | None = "request",
    received_at: datetime = NOW,
    review_status: ReviewStatus | None = None,
) -> EmailAnalysis:
    message = ProcessedMessage(
        id=message_id,
        provider="gmail",
        external_message_id=message_id,
        received_at=received_at,
        sender_address="sender@example.com",
        subject=subject,
        content_hash=_content_hash(subject),
    )
    analysis = EmailAnalysis(
        processed_message_id=message_id,
        summary=f"Summary of {subject}",
        priority="medium",
        action_required=True,
        category=category,
        confidence=0.9,
        model_name="qwen3:1.7b",
        prompt_version="v1",
    )
    analysis.processed_message = message
    if review_status is not None:
        analysis.review = EmailReview(email_analysis_id=message_id, review_status=review_status)
    return analysis


def test_empty_when_no_requests():
    info = make_analysis("m1", "Newsletter", category="information")
    items = build_open_items([info], replied_message_ids=set(), now=NOW)
    assert items == []


def test_replied_request_is_excluded():
    replied = make_analysis("m1", "Please approve")
    items = build_open_items([replied], replied_message_ids={"m1"}, now=NOW)
    assert items == []


def test_completed_or_dismissed_review_is_excluded():
    completed = make_analysis("m1", "Approve A", review_status=ReviewStatus.COMPLETED)
    dismissed = make_analysis("m2", "Approve B", review_status=ReviewStatus.DISMISSED)
    items = build_open_items([completed, dismissed], replied_message_ids=set(), now=NOW)
    assert items == []


def test_pending_or_reviewed_request_is_open():
    pending = make_analysis("m1", "Approve A")
    reviewed = make_analysis("m2", "Approve B", review_status=ReviewStatus.REVIEWED)
    items = build_open_items([pending, reviewed], replied_message_ids=set(), now=NOW)
    assert {i.message_id for i in items} == {"m1", "m2"}


def test_nag_level_thresholds():
    quiet = make_analysis("m1", "Fresh", received_at=NOW - timedelta(hours=1))
    noticeable = make_analysis("m2", "Aging", received_at=NOW - timedelta(hours=5))
    persistent = make_analysis("m3", "Old", received_at=NOW - timedelta(hours=25))

    items = {i.message_id: i for i in build_open_items([quiet, noticeable, persistent], replied_message_ids=set(), now=NOW)}

    assert items["m1"].nag_level == NagLevel.QUIET
    assert items["m2"].nag_level == NagLevel.NOTICEABLE
    assert items["m3"].nag_level == NagLevel.PERSISTENT


def test_sorted_oldest_first():
    newer = make_analysis("m1", "Newer", received_at=NOW - timedelta(hours=1))
    older = make_analysis("m2", "Older", received_at=NOW - timedelta(hours=10))

    items = build_open_items([newer, older], replied_message_ids=set(), now=NOW)

    assert [i.message_id for i in items] == ["m2", "m1"]
