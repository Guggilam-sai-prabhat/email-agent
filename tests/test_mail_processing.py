import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent.model_client import ModelNotInstalledError, ModelUnavailableError
from app.database.models import Base
from app.database.repository import EmailAnalysisRepository, ProcessedMessageRepository
from app.email.retrieval import EmailDocument
from app.services import mail_processing as mp
from app.services.mail_processing import MailProcessingService


@pytest.fixture(autouse=True)
def _shared_test_db(monkeypatch):
    """Route the service's get_session at an in-memory DB, shared across the
    service's multiple session-per-message pattern (SingletonThreadPool keeps
    every connection on one shared in-memory DB file). Test assertions reuse
    the same fake so they see what the service wrote."""
    from sqlalchemy.pool import SingletonThreadPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=SingletonThreadPool,
    )
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)

    from contextlib import contextmanager

    @contextmanager
    def _fake_get_session():
        session = TestSession()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(mp, "get_session", _fake_get_session)
    yield _fake_get_session


@pytest.fixture
def get_session(_shared_test_db):
    return _shared_test_db


def _document(message_id: str = "msg-1", body: str = "Please review the attached report.") -> EmailDocument:
    return EmailDocument(
        message_id=message_id,
        thread_id="thread-1",
        sender="alice@example.test",
        recipients=["bob@example.test"],
        subject="Report",
        date=datetime(2025, 7, 1, tzinfo=timezone.utc),
        body=body,
        truncated=False,
        quoted_history="",
        content_hash="deadbeef" * 8,
    )


def _valid_payload(**overrides) -> dict:
    payload = {
        "summary": "Alice asks Bob to review the report.",
        "priority": "medium",
        "priority_reason": "Routine request.",
        "action_required": True,
        "suggested_action": "Review the report",
        "action_owner": "Bob",
        "explicit_deadline": None,
        "deadline_text": None,
        "requires_reply": True,
        "category": "request",
        "uncertainty_notes": [],
        "confidence": 0.8,
    }
    payload.update(overrides)
    return payload


def _model_client(*responses: str) -> MagicMock:
    client = MagicMock()
    client.generate.side_effect = list(responses)
    return client


class FakeGmailService:
    """Fake of GmailRetrievalService's public surface only — no write methods exist."""

    def __init__(self, documents: dict[str, EmailDocument | None], candidate_ids: list[str] | None = None):
        self._documents = documents
        self._candidate_ids = candidate_ids if candidate_ids is not None else list(documents.keys())

    def list_candidate_message_ids(self) -> list[str]:
        return list(self._candidate_ids)

    def get_message_for_analysis(self, message_id: str):
        return self._documents.get(message_id)


def test_run_processes_a_single_message_end_to_end(get_session):
    gmail = FakeGmailService({"msg-1": _document()})
    client = _model_client(json.dumps(_valid_payload()))
    service = MailProcessingService(model_client=client, gmail_service=gmail)

    run = service.run()

    assert run.status == "completed"
    assert run.discovered_count == 1
    assert run.processed_count == 1
    assert run.skipped_count == 0
    assert run.failed_count == 0

    with get_session() as session:
        message_repo = ProcessedMessageRepository(session)
        analysis_repo = EmailAnalysisRepository(session)
        message = message_repo.get_by_external_id("gmail", "msg-1")
        assert message.processing_status == "completed"
        assert message.claimed_at is None
        analysis = analysis_repo.get_for_message(message.id)
        assert analysis.summary == "Alice asks Bob to review the report."
        assert analysis.priority == "medium"


def test_run_with_existing_run_id_updates_that_row_instead_of_creating_a_new_one(get_session):
    """Regression test: the API route creates a ProcessingRun row and returns its
    id to the client before the background thread starts. If run() ignored that
    id and created its own row, the caller's row would stay RUNNING forever
    while a second, untracked row actually completed — see app/api/routes.py's
    start_run/_run_in_background.
    """
    from app.database.models import ProcessingRun, RunStatus, TriggerType
    from app.database.repository import ProcessingRunRepository

    with get_session() as session:
        run_repo = ProcessingRunRepository(session)
        pre_created = run_repo.start(ProcessingRun(trigger_type=TriggerType.MANUAL))
        pre_created_id = pre_created.id

    gmail = FakeGmailService({"msg-1": _document()})
    client = _model_client(json.dumps(_valid_payload()))
    service = MailProcessingService(model_client=client, gmail_service=gmail)

    result = service.run(run_id=pre_created_id)

    assert result.id == pre_created_id
    assert result.status == "completed"
    assert result.discovered_count == 1
    assert result.processed_count == 1

    with get_session() as session:
        all_runs = session.query(ProcessingRun).all()
        assert len(all_runs) == 1  # no orphaned second row
        assert all_runs[0].id == pre_created_id
        assert all_runs[0].status == RunStatus.COMPLETED


def test_already_completed_message_is_skipped_not_reprocessed():
    gmail = FakeGmailService({"msg-1": _document()})
    client = _model_client(json.dumps(_valid_payload()))
    service = MailProcessingService(model_client=client, gmail_service=gmail)
    service.run()

    run2 = service.run()

    assert run2.discovered_count == 1
    assert run2.skipped_count == 1
    assert run2.processed_count == 0
    # the model must not be called again for an already-completed message
    assert client.generate.call_count == 1


def test_unavailable_message_is_skipped():
    gmail = FakeGmailService({"msg-1": None})
    client = _model_client()
    service = MailProcessingService(model_client=client, gmail_service=gmail)

    run = service.run()

    assert run.skipped_count == 1
    assert run.processed_count == 0
    assert client.generate.call_count == 0


def test_model_unavailable_marks_message_failed_and_continues_run(get_session):
    gmail = FakeGmailService({"msg-1": _document("msg-1"), "msg-2": _document("msg-2")})
    client = MagicMock()
    client.generate.side_effect = [ModelUnavailableError("no ollama"), json.dumps(_valid_payload())]
    service = MailProcessingService(model_client=client, gmail_service=gmail)

    run = service.run()

    assert run.failed_count == 1
    assert run.processed_count == 1

    with get_session() as session:
        message_repo = ProcessedMessageRepository(session)
        failed_message = message_repo.get_by_external_id("gmail", "msg-1")
        assert failed_message.processing_status == "failed"
        assert failed_message.last_error_code == "MODEL_UNAVAILABLE"
        assert failed_message.claimed_at is None


def test_model_not_installed_marks_message_failed(get_session):
    gmail = FakeGmailService({"msg-1": _document()})
    client = MagicMock()
    client.generate.side_effect = ModelNotInstalledError("model not pulled")
    service = MailProcessingService(model_client=client, gmail_service=gmail)

    run = service.run()

    assert run.failed_count == 1
    with get_session() as session:
        message_repo = ProcessedMessageRepository(session)
        message = message_repo.get_by_external_id("gmail", "msg-1")
        assert message.last_error_code == "MODEL_UNAVAILABLE"


def test_analysis_validation_failure_is_recorded_and_does_not_crash_run(get_session):
    gmail = FakeGmailService({"msg-1": _document("msg-1"), "msg-2": _document("msg-2")})
    # msg-1's model output never validates (even after retry); msg-2 succeeds.
    client = MagicMock()
    client.generate.side_effect = [
        "not json",
        "still not json",
        json.dumps(_valid_payload()),
    ]
    service = MailProcessingService(model_client=client, gmail_service=gmail)

    run = service.run()

    assert run.failed_count == 1
    assert run.processed_count == 1
    with get_session() as session:
        message_repo = ProcessedMessageRepository(session)
        failed_message = message_repo.get_by_external_id("gmail", "msg-1")
        assert failed_message.last_error_code == "ANALYSIS_VALIDATION_FAILED"


def test_unexpected_error_during_processing_is_isolated_per_message():
    gmail = FakeGmailService({"msg-1": _document("msg-1"), "msg-2": _document("msg-2")})
    client = _model_client(json.dumps(_valid_payload()))

    original = mp._sanitized_email_from_document
    call_count = {"n": 0}

    def _boom(document):
        call_count["n"] += 1
        if document.message_id == "msg-1":
            raise RuntimeError("boom")
        return original(document)

    service = MailProcessingService(model_client=client, gmail_service=gmail)
    with pytest.MonkeyPatch.context() as m:
        m.setattr(mp, "_sanitized_email_from_document", _boom)
        run = service.run()

    assert run.failed_count == 1
    assert run.processed_count == 1


def test_run_counters_never_store_full_email_body(get_session):
    gmail = FakeGmailService({"msg-1": _document(body="Sensitive project details go here.")})
    client = _model_client(json.dumps(_valid_payload()))
    service = MailProcessingService(model_client=client, gmail_service=gmail)

    service.run()

    with get_session() as session:
        message_repo = ProcessedMessageRepository(session)
        message = message_repo.get_by_external_id("gmail", "msg-1")
        # only a content hash is stored, never the body itself
        assert "Sensitive project details" not in message.subject
        assert not hasattr(message, "body")
        assert len(message.content_hash) == 64


def test_no_candidate_messages_completes_run_with_zero_counts():
    gmail = FakeGmailService({}, candidate_ids=[])
    client = _model_client()
    service = MailProcessingService(model_client=client, gmail_service=gmail)

    run = service.run()

    assert run.status == "completed"
    assert run.discovered_count == 0
    assert run.processed_count == 0
    assert run.skipped_count == 0
    assert run.failed_count == 0
