import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, EmailAnalysis, Priority, ProcessedMessage, ProcessingRun, TriggerType
from app.database.repository import (
    DuplicateMessageError,
    EmailAnalysisRepository,
    InvalidErrorCodeError,
    ProcessedMessageRepository,
    ProcessingRunRepository,
    sanitize_error_summary,
)


@pytest.fixture
def db_factory():
    """Yields a sessionmaker bound to a shared in-memory DB (SingletonThreadPool keeps all
    sessions on one connection), so multiple independently-created sessions can simulate
    separate worker processes claiming from the same table.
    """
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)


@pytest.fixture
def session(db_factory):
    db = db_factory()
    try:
        yield db
    finally:
        db.close()


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def make_message(**overrides) -> ProcessedMessage:
    defaults = dict(
        provider="gmail",
        external_message_id="msg-1",
        received_at=datetime.now(timezone.utc),
        sender_address="sender@example.com",
        subject="Hello",
        content_hash=_content_hash("body text"),
    )
    defaults.update(overrides)
    return ProcessedMessage(**defaults)


def test_create_message(session):
    repo = ProcessedMessageRepository(session)
    msg = repo.create(make_message())
    session.commit()

    assert msg.id is not None
    assert msg.processing_status == "discovered"
    assert msg.processing_attempts == 0
    assert msg.created_at.tzinfo is not None


def test_duplicate_provider_and_external_id_rejected(session):
    repo = ProcessedMessageRepository(session)
    repo.create(make_message())
    session.commit()

    with pytest.raises(DuplicateMessageError):
        repo.create(make_message())


def test_same_external_id_different_provider_allowed(session):
    repo = ProcessedMessageRepository(session)
    repo.create(make_message(provider="gmail"))
    session.commit()

    msg2 = repo.create(make_message(provider="outlook"))
    session.commit()
    assert msg2.id is not None


def test_exists_and_get_by_external_id(session):
    repo = ProcessedMessageRepository(session)
    repo.create(make_message())
    session.commit()

    assert repo.exists("gmail", "msg-1") is True
    assert repo.exists("gmail", "unknown") is False
    found = repo.get_by_external_id("gmail", "msg-1")
    assert found is not None
    assert found.sender_address == "sender@example.com"


def test_mark_completed_sets_status_and_timestamp(session):
    repo = ProcessedMessageRepository(session)
    msg = repo.create(make_message())
    session.commit()
    repo.claim(msg.id, claimed_by="run-1")
    session.commit()

    processed_at = datetime.now(timezone.utc)
    repo.mark_completed(msg, processed_at)
    session.commit()

    assert msg.processing_status == "completed"
    assert msg.processed_at == processed_at
    assert msg.claimed_at is None
    assert msg.claimed_by is None


def test_mark_failed_increments_attempts(session):
    repo = ProcessedMessageRepository(session)
    msg = repo.create(make_message())
    session.commit()

    repo.mark_failed(msg, "OLLAMA_TIMEOUT", error_summary="request timed out after 30s")
    session.commit()

    assert msg.processing_status == "failed"
    assert msg.processing_attempts == 1
    assert msg.last_error_code == "OLLAMA_TIMEOUT"
    assert msg.last_error_summary == "request timed out after 30s"
    assert msg.claimed_at is None


def test_invalid_processing_status_rejected(session):
    msg = make_message()
    session.add(msg)
    session.flush()
    with pytest.raises(IntegrityError):
        session.execute(
            ProcessedMessage.__table__.update()
            .where(ProcessedMessage.id == msg.id)
            .values(processing_status="bogus")
        )


def test_analysis_confidence_must_be_between_0_and_1(session):
    msg_repo = ProcessedMessageRepository(session)
    msg = msg_repo.create(make_message())
    session.commit()

    analysis_repo = EmailAnalysisRepository(session)
    bad_analysis = EmailAnalysis(
        processed_message_id=msg.id,
        summary="summary",
        priority=Priority.HIGH,
        action_required=True,
        confidence=1.5,
        model_name="qwen3:1.7b",
        prompt_version="v1",
    )
    with pytest.raises(IntegrityError):
        analysis_repo.create(bad_analysis)


def test_analysis_confidence_boundary_values_allowed(session):
    msg_repo = ProcessedMessageRepository(session)
    msg = msg_repo.create(make_message())
    session.commit()

    analysis_repo = EmailAnalysisRepository(session)
    analysis = analysis_repo.create(
        EmailAnalysis(
            processed_message_id=msg.id,
            summary="summary",
            priority=Priority.LOW,
            action_required=False,
            confidence=0.0,
            model_name="qwen3:1.7b",
            prompt_version="v1",
        )
    )
    session.commit()
    assert analysis.confidence == 0.0


def test_only_one_analysis_per_message(session):
    msg_repo = ProcessedMessageRepository(session)
    msg = msg_repo.create(make_message())
    session.commit()

    analysis_repo = EmailAnalysisRepository(session)
    analysis_repo.create(
        EmailAnalysis(
            processed_message_id=msg.id,
            summary="first",
            priority=Priority.LOW,
            action_required=False,
            confidence=0.5,
            model_name="qwen3:1.7b",
            prompt_version="v1",
        )
    )
    session.commit()

    with pytest.raises(IntegrityError):
        analysis_repo.create(
            EmailAnalysis(
                processed_message_id=msg.id,
                summary="second",
                priority=Priority.HIGH,
                action_required=True,
                confidence=0.9,
                model_name="qwen3:1.7b",
                prompt_version="v1",
            )
        )


def test_invalid_priority_rejected(session):
    msg_repo = ProcessedMessageRepository(session)
    msg = msg_repo.create(make_message())
    session.commit()

    session.add(
        EmailAnalysis(
            processed_message_id=msg.id,
            summary="summary",
            priority="critical",
            action_required=True,
            confidence=0.5,
            model_name="qwen3:1.7b",
            prompt_version="v1",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_analysis_cascade_deletes_with_message(session):
    msg_repo = ProcessedMessageRepository(session)
    msg = msg_repo.create(make_message())
    session.commit()

    analysis_repo = EmailAnalysisRepository(session)
    analysis_repo.create(
        EmailAnalysis(
            processed_message_id=msg.id,
            summary="summary",
            priority=Priority.LOW,
            action_required=False,
            confidence=0.5,
            model_name="qwen3:1.7b",
            prompt_version="v1",
        )
    )
    session.commit()

    session.delete(msg)
    session.commit()

    assert analysis_repo.get_for_message(msg.id) is None


def test_processing_run_lifecycle(session):
    run_repo = ProcessingRunRepository(session)
    run = run_repo.start(ProcessingRun(trigger_type=TriggerType.MANUAL, discovered_count=5))
    session.commit()

    assert run.status == "running"
    assert run.completed_at is None

    completed_at = datetime.now(timezone.utc)
    run_repo.complete(run, completed_at)
    session.commit()

    fetched = run_repo.get(run.id)
    assert fetched.status == "completed"
    assert fetched.completed_at == completed_at


def test_invalid_trigger_type_rejected(session):
    session.add(ProcessingRun(trigger_type="cron"))
    with pytest.raises(IntegrityError):
        session.commit()


# --- Claim state machine: concurrency and recovery ---------------------------------


def test_claim_moves_discovered_to_processing(session):
    repo = ProcessedMessageRepository(session)
    msg = repo.create(make_message())
    session.commit()

    won = repo.claim(msg.id, claimed_by="run-1")
    session.commit()

    assert won is True
    assert msg.processing_status == "processing"
    assert msg.claimed_by == "run-1"
    assert msg.claimed_at is not None


def test_duplicate_scheduler_runs_cannot_both_claim_same_message(db_factory):
    """Two schedulers race to claim the same DISCOVERED message; exactly one must win."""
    setup = db_factory()
    repo = ProcessedMessageRepository(setup)
    msg = repo.create(make_message())
    setup.commit()
    message_id = msg.id
    setup.close()

    session_a = db_factory()
    session_b = db_factory()
    try:
        repo_a = ProcessedMessageRepository(session_a)
        repo_b = ProcessedMessageRepository(session_b)

        won_a = repo_a.claim(message_id, claimed_by="run-a")
        session_a.commit()

        won_b = repo_b.claim(message_id, claimed_by="run-b")
        session_b.commit()

        assert won_a is True
        assert won_b is False

        final = repo_b.get_by_external_id("gmail", "msg-1")
        assert final.claimed_by == "run-a"
        assert final.processing_status == "processing"
    finally:
        session_a.close()
        session_b.close()


def test_claim_rejects_already_processing_message(session):
    repo = ProcessedMessageRepository(session)
    msg = repo.create(make_message())
    session.commit()
    assert repo.claim(msg.id, claimed_by="run-1") is True
    session.commit()

    won_again = repo.claim(msg.id, claimed_by="run-2")
    session.commit()

    assert won_again is False


def test_completed_message_not_reclaimable_without_explicit_reprocess(session):
    repo = ProcessedMessageRepository(session)
    msg = repo.create(make_message())
    session.commit()
    repo.claim(msg.id, claimed_by="run-1")
    repo.mark_completed(msg, datetime.now(timezone.utc))
    session.commit()

    assert repo.claim(msg.id, claimed_by="run-2") is False

    repo.reprocess(msg)
    session.commit()

    assert msg.processing_status == "discovered"
    assert repo.claim(msg.id, claimed_by="run-2") is True


def test_failed_message_retryable_up_to_configured_limit(session):
    repo = ProcessedMessageRepository(session)
    msg = repo.create(make_message())
    session.commit()

    for attempt in range(3):
        assert repo.claim(msg.id, claimed_by="run-1", max_attempts=3) is True
        session.commit()
        repo.mark_failed(msg, "OLLAMA_TIMEOUT", error_summary="timed out")
        session.commit()
        assert msg.processing_attempts == attempt + 1

    # attempts == max_attempts now: no more retries allowed
    assert repo.claim(msg.id, claimed_by="run-1", max_attempts=3) is False


def test_stale_processing_claim_recoverable_after_timeout(session):
    """Simulates a crash mid-PROCESSING: the claim is old enough to be considered stale,
    so a later claim call (e.g. after app restart) can reclaim and retry the message.
    """
    repo = ProcessedMessageRepository(session)
    msg = repo.create(make_message())
    session.commit()

    long_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    repo.claim(msg.id, claimed_by="run-1", now=long_ago)
    session.commit()
    assert msg.processing_status == "processing"

    # Not yet stale: still within the timeout window.
    still_fresh = repo.claim(
        msg.id, claimed_by="run-2", now=long_ago + timedelta(seconds=1), processing_timeout_seconds=600
    )
    session.commit()
    assert still_fresh is False

    # Past the timeout window: recoverable.
    recovered = repo.claim(
        msg.id, claimed_by="run-2", now=long_ago + timedelta(seconds=601), processing_timeout_seconds=600
    )
    session.commit()

    assert recovered is True
    assert msg.claimed_by == "run-2"
    assert msg.processing_status == "processing"


def test_mark_failed_rejects_non_whitelisted_error_code(session):
    repo = ProcessedMessageRepository(session)
    msg = repo.create(make_message())
    session.commit()
    repo.claim(msg.id, claimed_by="run-1")

    with pytest.raises(InvalidErrorCodeError):
        repo.mark_failed(msg, "raw exception: " + "Connection to user@example.com failed")


def test_sanitize_error_summary_truncates_and_strips_control_chars():
    raw = "line1\x00\x1b[31mline2" + ("x" * 600)
    cleaned = sanitize_error_summary(raw)

    assert "\x00" not in cleaned
    assert "\x1b" not in cleaned
    assert len(cleaned) <= 500


def test_error_summary_never_stores_email_content_by_contract(session):
    """This test documents the contract (see mark_failed docstring): callers pass a short
    developer-written summary, never the email subject/body. It does not — and cannot —
    detect a caller violating that contract; it only verifies the column stores exactly
    what sanitize_error_summary produced.
    """
    repo = ProcessedMessageRepository(session)
    msg = repo.create(make_message(subject="Wire transfer instructions: acct 12345"))
    session.commit()
    repo.claim(msg.id, claimed_by="run-1")

    repo.mark_failed(msg, "MODEL_TIMEOUT", error_summary="model call exceeded 30s timeout")
    session.commit()

    assert msg.last_error_summary == "model call exceeded 30s timeout"
    assert "12345" not in (msg.last_error_summary or "")
    assert "Wire transfer" not in (msg.last_error_summary or "")
