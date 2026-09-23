from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class UTCDateTime(TypeDecorator):
    """DateTime that round-trips as timezone-aware UTC.

    SQLite has no native timezone-aware datetime type, so it stores naive
    strings and returns naive datetimes; this re-attaches UTC on read.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, _dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime passed for a UTC timestamp column")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, _dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


class ProcessingStatus(str, enum.Enum):
    DISCOVERED = "discovered"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Priority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TriggerType(str, enum.Enum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ReviewStatus(str, enum.Enum):
    PENDING = "pending"
    REVIEWED = "reviewed"
    COMPLETED = "completed"
    DISMISSED = "dismissed"


class ProcessedMessage(Base):
    __tablename__ = "processed_messages"
    __table_args__ = (
        UniqueConstraint("provider", "external_message_id", name="uq_provider_external_message_id"),
        CheckConstraint(
            "processing_status IN ('discovered', 'processing', 'completed', 'failed', 'skipped')",
            name="ck_processed_messages_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    external_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    sender_address: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    # Sha256 hex digest of the body — used to detect duplicate content, not the body itself.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        String(20), nullable=False, default=ProcessingStatus.DISCOVERED
    )
    # Set when a worker claims the message (moves it to PROCESSING) and cleared on
    # COMPLETED/FAILED/SKIPPED. Used to detect and recover stale claims after a crash.
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Opaque identifier of the worker/run that holds the claim (e.g. ProcessingRun.id).
    claimed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    processing_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Sanitized, human-readable summary only — never raw email content or exceptions. See mark_failed().
    last_error_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    analysis: Mapped["EmailAnalysis | None"] = relationship(
        back_populates="processed_message", uselist=False, cascade="all, delete-orphan"
    )


class EmailAnalysis(Base):
    __tablename__ = "email_analyses"
    __table_args__ = (
        # One active/final analysis per message for the MVP.
        UniqueConstraint("processed_message_id", name="uq_email_analyses_processed_message_id"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_email_analyses_confidence_range"),
        CheckConstraint("priority IN ('low', 'medium', 'high', 'urgent')", name="ck_email_analyses_priority"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    processed_message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("processed_messages.id", ondelete="CASCADE"), nullable=False
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[Priority] = mapped_column(String(10), nullable=False)
    priority_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_required: Mapped[bool] = mapped_column(nullable=False)
    suggested_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    explicit_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    confidence: Mapped[float] = mapped_column(nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utcnow)

    processed_message: Mapped["ProcessedMessage"] = relationship(back_populates="analysis")
    review: Mapped["EmailReview | None"] = relationship(
        back_populates="analysis", uselist=False, cascade="all, delete-orphan"
    )


class EmailReview(Base):
    """User's review decision for one EmailAnalysis. Kept separate from
    EmailAnalysis so the model's original recommendation is never mutated by
    a user action — see ReviewAuditLog for the change history. Local only:
    nothing here is ever written back to Gmail.
    """

    __tablename__ = "email_reviews"
    __table_args__ = (
        UniqueConstraint("email_analysis_id", name="uq_email_reviews_email_analysis_id"),
        CheckConstraint(
            "review_status IN ('pending', 'reviewed', 'completed', 'dismissed')",
            name="ck_email_reviews_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email_analysis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("email_analyses.id", ondelete="CASCADE"), nullable=False
    )
    review_status: Mapped[ReviewStatus] = mapped_column(
        String(20), nullable=False, default=ReviewStatus.PENDING
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    user_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    analysis: Mapped["EmailAnalysis"] = relationship(back_populates="review")


class ReviewAuditLog(Base):
    """Append-only local audit trail of review status changes. Never sent to Gmail."""

    __tablename__ = "review_audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email_analysis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("email_analyses.id", ondelete="CASCADE"), nullable=False
    )
    previous_status: Mapped[ReviewStatus | None] = mapped_column(String(20), nullable=True)
    new_status: Mapped[ReviewStatus] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utcnow)


class ProcessingRun(Base):
    __tablename__ = "processing_runs"
    __table_args__ = (
        CheckConstraint("trigger_type IN ('manual', 'scheduled')", name="ck_processing_runs_trigger_type"),
        CheckConstraint("status IN ('running', 'completed', 'failed')", name="ck_processing_runs_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    trigger_type: Mapped[TriggerType] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[RunStatus] = mapped_column(String(20), nullable=False, default=RunStatus.RUNNING)
    discovered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
