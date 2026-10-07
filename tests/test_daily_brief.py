import hashlib
from datetime import datetime, timedelta, timezone

from app.database.models import EmailAnalysis, Priority, ProcessedMessage
from app.services.daily_brief import build_daily_brief

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def make_analysis(
    message_id: str,
    subject: str,
    *,
    priority: Priority = Priority.MEDIUM,
    category: str | None = "information",
    action_required: bool = False,
    explicit_deadline: datetime | None = None,
) -> EmailAnalysis:
    message = ProcessedMessage(
        id=message_id,
        provider="gmail",
        external_message_id=message_id,
        received_at=NOW,
        sender_address="sender@example.com",
        subject=subject,
        content_hash=_content_hash(subject),
    )
    analysis = EmailAnalysis(
        processed_message_id=message_id,
        summary=f"Summary of {subject}",
        priority=priority,
        priority_reason="stated in email",
        action_required=action_required,
        category=category,
        explicit_deadline=explicit_deadline,
        confidence=0.9,
        model_name="qwen3:1.7b",
        prompt_version="v1",
    )
    analysis.processed_message = message
    return analysis


def test_empty_brief():
    brief = build_daily_brief([], now=NOW)
    assert brief.urgent == []
    assert brief.deadlines == []
    assert brief.needs_reply == []
    assert brief.other_action_items == []
    assert brief.informational == []
    assert brief.suggested_order == []


def test_normal_case_buckets_by_stored_fields():
    urgent = make_analysis("m1", "Server down", priority=Priority.URGENT, category="alert", action_required=True)
    request = make_analysis("m2", "Please approve", category="request", action_required=True)
    action = make_analysis("m3", "Update your records", category="other", action_required=True)
    info = make_analysis("m4", "Newsletter", category="information", action_required=False)

    brief = build_daily_brief([urgent, request, action, info], now=NOW)

    assert [i.message_id for i in brief.urgent] == ["m1"]
    assert [i.message_id for i in brief.needs_reply] == ["m2"]
    # m1 is urgent+action_required but not a "request" category, so it also
    # lands in other_action_items rather than being silently dropped.
    assert [i.message_id for i in brief.other_action_items] == ["m1", "m3"]
    assert [i.message_id for i in brief.informational] == ["m4"]
    # every item carries its source id and subject
    for item in brief.suggested_order:
        assert item.message_id
        assert item.subject


def test_conflicting_signals_urgent_and_reply_both_recorded():
    # urgent + also a request: must appear in both buckets, not silently dropped from one.
    both = make_analysis(
        "m1", "Urgent approval needed", priority=Priority.URGENT, category="request", action_required=True
    )
    brief = build_daily_brief([both], now=NOW)

    assert [i.message_id for i in brief.urgent] == ["m1"]
    assert [i.message_id for i in brief.needs_reply] == ["m1"]
    assert brief.other_action_items == []


def test_overdue_deadline_sorts_first_and_is_flagged():
    overdue = make_analysis("m1", "Old invoice", explicit_deadline=NOW - timedelta(days=2))
    upcoming = make_analysis("m2", "Upcoming review", explicit_deadline=NOW + timedelta(days=1))
    no_deadline = make_analysis("m3", "FYI", priority=Priority.URGENT)

    brief = build_daily_brief([no_deadline, upcoming, overdue], now=NOW)

    assert brief.suggested_order[0].message_id == "m1"
    assert brief.suggested_order[0].is_overdue is True
    assert brief.suggested_order[1].message_id == "m2"
    assert brief.suggested_order[1].is_overdue is False
    assert [i.message_id for i in brief.deadlines] == ["m1", "m2"]


def test_no_deadline_never_invents_one():
    analysis = make_analysis("m1", "No date mentioned")
    brief = build_daily_brief([analysis], now=NOW)

    assert brief.deadlines == []
    assert brief.suggested_order[0].explicit_deadline is None
