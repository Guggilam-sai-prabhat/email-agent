"""Daily brief: deterministic aggregation of already-validated EmailAnalysis rows.

Local dashboard only — never emailed, never pushed as a notification (see
app/api/routes.py). Consumes stored, sanitized analysis fields (summary,
priority, category, explicit_deadline, ...) only; never raw email bodies and
never calls the model. All facts here are read directly from EmailAnalysis /
ProcessedMessage columns set by app.agent.analysis — nothing is invented.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.database.models import EmailAnalysis, Priority

_PRIORITY_RANK = {Priority.URGENT: 0, Priority.HIGH: 1, Priority.MEDIUM: 2, Priority.LOW: 3}


@dataclass(frozen=True)
class BriefItem:
    message_id: str
    subject: str
    summary: str
    priority: Priority
    category: str | None
    explicit_deadline: datetime | None
    is_overdue: bool


@dataclass
class DailyBrief:
    generated_at: datetime
    urgent: list[BriefItem] = field(default_factory=list)
    deadlines: list[BriefItem] = field(default_factory=list)
    needs_reply: list[BriefItem] = field(default_factory=list)
    other_action_items: list[BriefItem] = field(default_factory=list)
    informational: list[BriefItem] = field(default_factory=list)
    # Explicit facts (message_id + subject) in the deterministic priority/deadline
    # order computed below. Distinct from any model recommendation.
    suggested_order: list[BriefItem] = field(default_factory=list)


def _to_item(analysis: EmailAnalysis, *, now: datetime) -> BriefItem:
    message = analysis.processed_message
    deadline = analysis.explicit_deadline
    return BriefItem(
        message_id=message.id,
        subject=message.subject,
        summary=analysis.summary,
        priority=Priority(analysis.priority),
        category=analysis.category,
        explicit_deadline=deadline,
        is_overdue=deadline is not None and deadline < now,
    )


def _sort_key(item: BriefItem) -> tuple:
    # Overdue first, then earliest explicit deadline, then priority rank.
    # Items with no deadline sort after those with one.
    return (
        0 if item.is_overdue else 1,
        item.explicit_deadline or datetime.max.replace(tzinfo=timezone.utc),
        _PRIORITY_RANK[item.priority],
    )


def build_daily_brief(analyses: list[EmailAnalysis], *, now: datetime | None = None) -> DailyBrief:
    """Pure aggregation: buckets validated analyses by explicit stored fields.

    No deadline or priority is invented here — every value comes straight from
    a persisted EmailAnalysis row produced by the validated analysis pipeline.
    """
    now = now or datetime.now(timezone.utc)
    brief = DailyBrief(generated_at=now)

    for analysis in analyses:
        item = _to_item(analysis, now=now)

        if item.priority == Priority.URGENT:
            brief.urgent.append(item)
        if item.explicit_deadline is not None:
            brief.deadlines.append(item)

        # requires_reply isn't a persisted column (see EmailAnalysisResult vs.
        # EmailAnalysis in app/database/models.py), so category == "request" is
        # the stored, explicit proxy for "needs a reply".
        if analysis.category == "request":
            brief.needs_reply.append(item)
        elif analysis.action_required:
            brief.other_action_items.append(item)
        else:
            brief.informational.append(item)

    brief.suggested_order = sorted((_to_item(a, now=now) for a in analyses), key=_sort_key)
    brief.deadlines.sort(key=_sort_key)
    return brief
