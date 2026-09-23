from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, EmailAnalysis, ProcessedMessage, ReviewAuditLog, ReviewStatus
from app.database.repository import EmailReviewRepository, InvalidReviewTransitionError


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()


def _make_analysis(session) -> EmailAnalysis:
    message = ProcessedMessage(
        provider="gmail",
        external_message_id="msg-1",
        received_at=datetime.now(timezone.utc),
        sender_address="alice@example.test",
        subject="Report",
        content_hash="a" * 64,
    )
    session.add(message)
    session.flush()
    analysis = EmailAnalysis(
        processed_message_id=message.id,
        summary="Alice asks Bob to review the report.",
        priority="medium",
        action_required=True,
        confidence=0.8,
        model_name="qwen3:1.7b",
        prompt_version="v1",
    )
    session.add(analysis)
    session.flush()
    return analysis


def test_get_or_create_starts_in_pending(session):
    analysis = _make_analysis(session)
    repo = EmailReviewRepository(session)

    review = repo.get_or_create(analysis.id)

    assert review.review_status == ReviewStatus.PENDING
    assert review.reviewed_at is None
    assert review.completed_at is None
    assert review.dismissed_at is None
    assert review.user_note is None


def test_get_or_create_is_idempotent(session):
    analysis = _make_analysis(session)
    repo = EmailReviewRepository(session)

    first = repo.get_or_create(analysis.id)
    second = repo.get_or_create(analysis.id)

    assert first.id == second.id


def test_pending_to_reviewed_stamps_reviewed_at(session):
    analysis = _make_analysis(session)
    repo = EmailReviewRepository(session)
    review = repo.get_or_create(analysis.id)

    repo.set_status(review, ReviewStatus.REVIEWED)

    assert review.review_status == ReviewStatus.REVIEWED
    assert review.reviewed_at is not None
    assert review.completed_at is None


def test_pending_to_completed_is_allowed_directly(session):
    analysis = _make_analysis(session)
    repo = EmailReviewRepository(session)
    review = repo.get_or_create(analysis.id)

    repo.set_status(review, ReviewStatus.COMPLETED)

    assert review.review_status == ReviewStatus.COMPLETED
    assert review.completed_at is not None


def test_reviewed_to_completed_is_allowed(session):
    analysis = _make_analysis(session)
    repo = EmailReviewRepository(session)
    review = repo.get_or_create(analysis.id)
    repo.set_status(review, ReviewStatus.REVIEWED)

    repo.set_status(review, ReviewStatus.COMPLETED)

    assert review.review_status == ReviewStatus.COMPLETED


def test_pending_to_dismissed_is_allowed(session):
    analysis = _make_analysis(session)
    repo = EmailReviewRepository(session)
    review = repo.get_or_create(analysis.id)

    repo.set_status(review, ReviewStatus.DISMISSED)

    assert review.review_status == ReviewStatus.DISMISSED
    assert review.dismissed_at is not None


def test_completed_is_terminal(session):
    analysis = _make_analysis(session)
    repo = EmailReviewRepository(session)
    review = repo.get_or_create(analysis.id)
    repo.set_status(review, ReviewStatus.COMPLETED)

    with pytest.raises(InvalidReviewTransitionError):
        repo.set_status(review, ReviewStatus.REVIEWED)
    with pytest.raises(InvalidReviewTransitionError):
        repo.set_status(review, ReviewStatus.DISMISSED)


def test_dismissed_is_terminal(session):
    analysis = _make_analysis(session)
    repo = EmailReviewRepository(session)
    review = repo.get_or_create(analysis.id)
    repo.set_status(review, ReviewStatus.DISMISSED)

    with pytest.raises(InvalidReviewTransitionError):
        repo.set_status(review, ReviewStatus.COMPLETED)


def test_invalid_transition_does_not_write_audit_log(session):
    analysis = _make_analysis(session)
    repo = EmailReviewRepository(session)
    review = repo.get_or_create(analysis.id)
    repo.set_status(review, ReviewStatus.COMPLETED)

    with pytest.raises(InvalidReviewTransitionError):
        repo.set_status(review, ReviewStatus.REVIEWED)

    entries = session.execute(select(ReviewAuditLog).where(ReviewAuditLog.email_analysis_id == analysis.id)).scalars().all()
    assert len(entries) == 1  # only the pending -> completed transition


def test_set_status_writes_audit_log_entry(session):
    analysis = _make_analysis(session)
    repo = EmailReviewRepository(session)
    review = repo.get_or_create(analysis.id)

    repo.set_status(review, ReviewStatus.REVIEWED)

    entries = session.execute(select(ReviewAuditLog).where(ReviewAuditLog.email_analysis_id == analysis.id)).scalars().all()
    assert len(entries) == 1
    assert entries[0].previous_status == ReviewStatus.PENDING
    assert entries[0].new_status == ReviewStatus.REVIEWED


def test_set_note_does_not_touch_status_or_write_audit_log(session):
    analysis = _make_analysis(session)
    repo = EmailReviewRepository(session)
    review = repo.get_or_create(analysis.id)

    repo.set_note(review, "Call back Thursday")

    assert review.user_note == "Call back Thursday"
    assert review.review_status == ReviewStatus.PENDING
    entries = session.execute(select(ReviewAuditLog).where(ReviewAuditLog.email_analysis_id == analysis.id)).scalars().all()
    assert entries == []


def test_setting_review_status_never_touches_the_analysis_row(session):
    analysis = _make_analysis(session)
    original_summary = analysis.summary
    original_priority = analysis.priority
    repo = EmailReviewRepository(session)
    review = repo.get_or_create(analysis.id)

    repo.set_status(review, ReviewStatus.COMPLETED)
    repo.set_note(review, "done")

    refreshed = session.get(EmailAnalysis, analysis.id)
    assert refreshed.summary == original_summary
    assert refreshed.priority == original_priority
