"""Message processing state machine: DISCOVERED -> PROCESSING -> COMPLETED/FAILED/SKIPPED.

Claim safety on SQLite (current target):
    `claim()` is a single `UPDATE ... WHERE <still claimable>` statement. SQLite
    serializes writers at the database-file level (one write transaction at a
    time), so this statement is atomic: two callers racing to claim the same row
    can't both succeed, even without SELECT ... FOR UPDATE (which SQLite doesn't
    support). This is sufficient for one process with multiple threads/asyncio
    tasks sharing one SQLite file, which is this app's current deployment model.

What changes for PostgreSQL + multiple service instances:
    - The same `UPDATE ... WHERE` pattern is still correct and remains the
      preferred approach (Postgres also atomically serializes conflicting
      UPDATEs on a row via MVCC + row locking) — no rewrite needed for
      correctness. Optionally add `SELECT ... FOR UPDATE SKIP LOCKED` on a
      pre-selection query if you need to claim a *batch* of messages per
      worker without them fighting over the same candidate rows.
    - `claimed_by` should become a real worker/instance identifier (hostname +
      PID, or a UUID per process), not just the ProcessingRun id, so stale
      claims can be attributed when debugging multi-instance races.
    - The stale-claim recovery window (`processing_timeout_seconds`) becomes
      more important to tune carefully: with multiple instances, clock skew
      between hosts means `now` should come from `SELECT now()` on the DB
      server (already true here, since SQLAlchemy passes the Python-side
      `now`, but for Postgres prefer `NOW()` server-side, e.g. via
      `func.now()`) rather than each instance's local clock.
    - Connection pool sizing (`create_engine(..., pool_size=...)`) needs to be
      set explicitly for Postgres; SQLite's default pool assumptions don't
      apply.
    - `check_same_thread=False` and the SQLite-only foreign-key PRAGMA in
      session.py become no-ops/irrelevant and can be removed behind a dialect
      check.
"""

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import (
    EmailAnalysis,
    EmailReview,
    ProcessedMessage,
    ProcessingRun,
    ProcessingStatus,
    ReviewAuditLog,
    ReviewStatus,
    RunStatus,
)

# Whitelist error codes so no exception text / stack trace can ever land in the column.
_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_MAX_ERROR_SUMMARY_LENGTH = 500


class DuplicateMessageError(Exception):
    """Raised when a (provider, external_message_id) pair already exists."""


class InvalidErrorCodeError(Exception):
    """Raised when an error code doesn't match the safe UPPER_SNAKE_CASE whitelist pattern."""


def sanitize_error_summary(summary: str | None) -> str | None:
    """Truncate and strip control characters from an error summary.

    Callers must never pass raw email content (subject/body/sender) or raw
    exception text here — only a short, developer-written, human-safe summary
    (e.g. "request timed out after 30s"). This function only guards against
    accidental control-character/length issues, not against a caller passing
    sensitive content by mistake.
    """
    if summary is None:
        return None
    cleaned = "".join(ch for ch in summary if ch == " " or ch.isprintable())
    return cleaned[:_MAX_ERROR_SUMMARY_LENGTH]


class ProcessedMessageRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_external_id(self, provider: str, external_message_id: str) -> ProcessedMessage | None:
        stmt = select(ProcessedMessage).where(
            ProcessedMessage.provider == provider,
            ProcessedMessage.external_message_id == external_message_id,
        )
        return self.session.scalars(stmt).first()

    def exists(self, provider: str, external_message_id: str) -> bool:
        return self.get_by_external_id(provider, external_message_id) is not None

    def create(self, message: ProcessedMessage) -> ProcessedMessage:
        """Insert a new message (starts in DISCOVERED). Raises DuplicateMessageError on collision."""
        self.session.add(message)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise DuplicateMessageError(
                f"message {message.provider}:{message.external_message_id} already processed"
            ) from exc
        return message

    def claim(
        self,
        message_id: str,
        claimed_by: str,
        *,
        now: datetime | None = None,
        max_attempts: int | None = None,
        processing_timeout_seconds: int | None = None,
    ) -> bool:
        """Atomically move a message from a claimable state into PROCESSING.

        A message is claimable if it is DISCOVERED, FAILED (with attempts left),
        or PROCESSING with a claim older than the processing timeout (crash
        recovery). The UPDATE ... WHERE is a single statement, so this is safe
        under concurrent callers even without an explicit row lock: only one
        writer's UPDATE can match a still-claimable row before another writer's
        UPDATE (against the same expected old state) also flips it — the second
        one affects zero rows. Call session.commit() right after this to end the
        transaction and make the claim visible to other connections.

        Returns True if this call won the claim, False if the message was not
        claimable (already claimed by someone else, completed, skipped, or out
        of retries).
        """
        now = now or datetime.now(timezone.utc)
        max_attempts = max_attempts if max_attempts is not None else settings.max_processing_attempts
        timeout = (
            processing_timeout_seconds
            if processing_timeout_seconds is not None
            else settings.processing_timeout_seconds
        )
        stale_cutoff = now - timedelta(seconds=timeout)

        stmt = (
            update(ProcessedMessage)
            .where(
                ProcessedMessage.id == message_id,
                or_(
                    ProcessedMessage.processing_status == ProcessingStatus.DISCOVERED,
                    (ProcessedMessage.processing_status == ProcessingStatus.FAILED)
                    & (ProcessedMessage.processing_attempts < max_attempts),
                    (ProcessedMessage.processing_status == ProcessingStatus.PROCESSING)
                    & (ProcessedMessage.claimed_at < stale_cutoff),
                ),
            )
            .values(processing_status=ProcessingStatus.PROCESSING, claimed_at=now, claimed_by=claimed_by)
        )
        result = self.session.execute(stmt)
        self.session.flush()
        return result.rowcount == 1

    def mark_completed(self, message: ProcessedMessage, processed_at: datetime) -> None:
        message.processing_status = ProcessingStatus.COMPLETED
        message.processed_at = processed_at
        message.claimed_at = None
        message.claimed_by = None
        self.session.flush()

    def mark_failed(self, message: ProcessedMessage, error_code: str, error_summary: str | None = None) -> None:
        """Record a failed attempt. error_code must be UPPER_SNAKE_CASE; error_summary is
        sanitized/truncated. Never pass raw email content or exception text — see
        sanitize_error_summary().
        """
        if not _ERROR_CODE_RE.match(error_code):
            raise InvalidErrorCodeError(f"error code must match {_ERROR_CODE_RE.pattern!r}: {error_code!r}")
        message.processing_status = ProcessingStatus.FAILED
        message.processing_attempts += 1
        message.last_error_code = error_code
        message.last_error_summary = sanitize_error_summary(error_summary)
        message.claimed_at = None
        message.claimed_by = None
        self.session.flush()

    def mark_skipped(self, message: ProcessedMessage) -> None:
        message.processing_status = ProcessingStatus.SKIPPED
        message.claimed_at = None
        message.claimed_by = None
        self.session.flush()

    def reprocess(self, message: ProcessedMessage) -> None:
        """Explicitly reset a COMPLETED/SKIPPED/FAILED message back to DISCOVERED so it can be
        claimed again. This is the only way a COMPLETED message becomes eligible for processing.
        """
        message.processing_status = ProcessingStatus.DISCOVERED
        message.claimed_at = None
        message.claimed_by = None
        message.processing_attempts = 0
        message.last_error_code = None
        message.last_error_summary = None
        self.session.flush()


class EmailAnalysisRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_for_message(self, processed_message_id: str) -> EmailAnalysis | None:
        stmt = select(EmailAnalysis).where(EmailAnalysis.processed_message_id == processed_message_id)
        return self.session.scalars(stmt).first()

    def create(self, analysis: EmailAnalysis) -> EmailAnalysis:
        """Insert the analysis for a message. Raises IntegrityError if one already exists (one-per-message)."""
        self.session.add(analysis)
        self.session.flush()
        return analysis


class InvalidReviewTransitionError(Exception):
    """Raised when a review status change isn't allowed from the current status."""


# Allowed review_status transitions. pending is the only starting state (set
# implicitly by get_or_create); every other transition must be listed here.
_ALLOWED_REVIEW_TRANSITIONS: dict[ReviewStatus, set[ReviewStatus]] = {
    ReviewStatus.PENDING: {ReviewStatus.REVIEWED, ReviewStatus.COMPLETED, ReviewStatus.DISMISSED},
    ReviewStatus.REVIEWED: {ReviewStatus.COMPLETED, ReviewStatus.DISMISSED},
    ReviewStatus.COMPLETED: set(),
    ReviewStatus.DISMISSED: set(),
}

_STATUS_TIMESTAMP_FIELD: dict[ReviewStatus, str] = {
    ReviewStatus.REVIEWED: "reviewed_at",
    ReviewStatus.COMPLETED: "completed_at",
    ReviewStatus.DISMISSED: "dismissed_at",
}


class EmailReviewRepository:
    """User review decisions. Every write here is local-only and driven by an
    explicit user API request — nothing in this class is reachable from model
    output or from Gmail sync code. See EmailReview/ReviewAuditLog in models.py.
    """

    def __init__(self, session: Session):
        self.session = session

    def get_for_analysis(self, email_analysis_id: str) -> EmailReview | None:
        stmt = select(EmailReview).where(EmailReview.email_analysis_id == email_analysis_id)
        return self.session.scalars(stmt).first()

    def get_or_create(self, email_analysis_id: str) -> EmailReview:
        review = self.get_for_analysis(email_analysis_id)
        if review is not None:
            return review
        review = EmailReview(email_analysis_id=email_analysis_id, review_status=ReviewStatus.PENDING)
        self.session.add(review)
        self.session.flush()
        return review

    def set_status(self, review: EmailReview, new_status: ReviewStatus, *, now: datetime | None = None) -> EmailReview:
        """Transition review_status, stamping the matching *_at field and writing
        an audit log entry. Raises InvalidReviewTransitionError if the transition
        isn't allowed from the review's current status.
        """
        current = ReviewStatus(review.review_status)
        if new_status not in _ALLOWED_REVIEW_TRANSITIONS.get(current, set()):
            raise InvalidReviewTransitionError(f"cannot transition review from {current.value!r} to {new_status.value!r}")

        now = now or datetime.now(timezone.utc)
        review.review_status = new_status
        timestamp_field = _STATUS_TIMESTAMP_FIELD.get(new_status)
        if timestamp_field is not None:
            setattr(review, timestamp_field, now)

        self.session.add(
            ReviewAuditLog(
                email_analysis_id=review.email_analysis_id,
                previous_status=current,
                new_status=new_status,
            )
        )
        self.session.flush()
        return review

    def set_note(self, review: EmailReview, note: str | None) -> EmailReview:
        """Sets the private local note. Independent of review_status transitions."""
        review.user_note = note
        self.session.flush()
        return review


class ProcessingRunRepository:
    def __init__(self, session: Session):
        self.session = session

    def start(self, run: ProcessingRun) -> ProcessingRun:
        self.session.add(run)
        self.session.flush()
        return run

    def complete(self, run: ProcessingRun, completed_at: datetime, status: RunStatus = RunStatus.COMPLETED) -> None:
        run.completed_at = completed_at
        run.status = status
        self.session.flush()

    def get(self, run_id: str) -> ProcessingRun | None:
        return self.session.get(ProcessingRun, run_id)
