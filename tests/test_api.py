from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.model_client import ModelUnavailableError
from app.api import deps as api_deps
from app.database.models import Base, EmailAnalysis, ProcessedMessage, ProcessingRun, RunStatus, TriggerType
from app.database import session as db_session
from app.email.retrieval import EmailDocument
from app.main import app


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)

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

    def _override_get_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[api_deps.get_db] = _override_get_db
    orig_get_session = db_session.get_session
    db_session.get_session = _fake_get_session
    from app.api import routes as api_routes

    api_routes.get_session = _fake_get_session

    yield TestSession

    app.dependency_overrides.pop(api_deps.get_db, None)
    db_session.get_session = orig_get_session
    api_routes.get_session = orig_get_session


@pytest.fixture()
def client(db):
    return TestClient(app)


def _seed_message_with_analysis(db, **overrides) -> tuple[str, str]:
    session = db()
    message = ProcessedMessage(
        provider="gmail",
        external_message_id=overrides.get("external_message_id", "msg-1"),
        external_thread_id="thread-1",
        received_at=overrides.get("received_at", datetime(2025, 7, 1, tzinfo=timezone.utc)),
        sender_address=overrides.get("sender_address", "alice@example.test"),
        subject=overrides.get("subject", "Report"),
        content_hash="a" * 64,
        processing_status="completed",
    )
    session.add(message)
    session.flush()
    analysis = EmailAnalysis(
        processed_message_id=message.id,
        summary="Alice asks Bob to review the report.",
        priority=overrides.get("priority", "medium"),
        action_required=overrides.get("action_required", True),
        suggested_action="Review the report",
        category=overrides.get("category", "request"),
        confidence=overrides.get("confidence", 0.8),
        model_name="qwen3:1.7b",
        prompt_version="v1",
    )
    session.add(analysis)
    session.commit()
    return message.id, analysis.id


def test_get_status_reports_model_gmail_and_last_run(client, db, monkeypatch):
    from app.api import routes as api_routes

    monkeypatch.setattr(api_routes.OllamaModelClient, "is_ready", lambda self: True)
    monkeypatch.setattr(api_routes, "has_stored_token", lambda: True)

    session = db()
    session.add(
        ProcessingRun(
            trigger_type=TriggerType.MANUAL,
            status=RunStatus.COMPLETED,
            completed_at=datetime(2025, 7, 1, 12, 0, tzinfo=timezone.utc),
        )
    )
    session.commit()

    response = client.get("/api/v1/status")
    assert response.status_code == 200
    body = response.json()
    assert body["model_available"] is True
    assert body["gmail_authorized"] is True
    assert body["run_in_progress"] is False
    assert body["last_successful_run_at"] is not None


def test_get_status_reports_model_unavailable_and_no_gmail_token(client, db, monkeypatch):
    from app.api import routes as api_routes

    monkeypatch.setattr(api_routes.OllamaModelClient, "is_ready", lambda self: False)
    monkeypatch.setattr(api_routes, "has_stored_token", lambda: False)

    response = client.get("/api/v1/status")
    assert response.status_code == 200
    body = response.json()
    assert body["model_available"] is False
    assert body["gmail_authorized"] is False
    assert body["last_successful_run_at"] is None


def test_start_run_returns_run_id_and_prevents_overlap(client, db, monkeypatch):
    from app.api import routes as api_routes

    started = MagicMock()
    monkeypatch.setattr(api_routes.threading, "Thread", lambda target, args, daemon: started)

    response = client.post("/api/v1/runs")
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "running"
    run_id = body["run_id"]

    # a second run while the first is still RUNNING must be rejected
    response2 = client.post("/api/v1/runs")
    assert response2.status_code == 409
    assert "exception" not in response2.json()["detail"].lower()

    session = db()
    run = session.get(ProcessingRun, run_id)
    assert run is not None


def test_stale_running_run_is_reaped_as_failed_on_status_check(client, db, monkeypatch):
    from app.api import routes as api_routes

    monkeypatch.setattr(api_routes.settings, "run_timeout_seconds", 1)

    session = db()
    stale_run = ProcessingRun(
        trigger_type=TriggerType.MANUAL,
        status=RunStatus.RUNNING,
        started_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )
    session.add(stale_run)
    session.commit()
    run_id = stale_run.id

    response = client.get("/api/v1/status")
    assert response.status_code == 200
    assert response.json()["run_in_progress"] is False

    refreshed = db().get(ProcessingRun, run_id)
    assert refreshed.status == "failed"
    assert refreshed.completed_at is not None


def test_stale_run_reaping_lets_a_new_run_start(client, db, monkeypatch):
    from app.api import routes as api_routes

    monkeypatch.setattr(api_routes.settings, "run_timeout_seconds", 1)
    monkeypatch.setattr(api_routes.threading, "Thread", lambda target, args, daemon: MagicMock())

    session = db()
    session.add(
        ProcessingRun(
            trigger_type=TriggerType.MANUAL,
            status=RunStatus.RUNNING,
            started_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        )
    )
    session.commit()

    response = client.post("/api/v1/runs")
    assert response.status_code == 202


def test_recent_running_run_is_not_reaped(client, db, monkeypatch):
    from app.api import routes as api_routes

    monkeypatch.setattr(api_routes.settings, "run_timeout_seconds", 900)

    session = db()
    fresh_run = ProcessingRun(trigger_type=TriggerType.MANUAL, status=RunStatus.RUNNING)
    session.add(fresh_run)
    session.commit()
    run_id = fresh_run.id

    response = client.get("/api/v1/status")
    assert response.json()["run_in_progress"] is True

    refreshed = db().get(ProcessingRun, run_id)
    assert refreshed.status == "running"


def test_start_run_does_not_leave_an_orphaned_running_row_after_it_completes(client, db, monkeypatch):
    """Regression test for the bug where MailProcessingService.run() created its
    own second ProcessingRun row instead of updating the one start_run() already
    created and returned to the client — leaving that first row stuck RUNNING
    forever even though the actual work finished immediately.
    """
    from app.api import routes as api_routes

    def _run_immediately(target, args, daemon):
        target(*args)
        return MagicMock()

    monkeypatch.setattr(api_routes.threading, "Thread", _run_immediately)

    from app.services import mail_processing as mail_processing_module

    gmail_mock = MagicMock()
    gmail_mock.list_candidate_message_ids.return_value = []
    monkeypatch.setattr(mail_processing_module, "GmailRetrievalService", lambda: gmail_mock)
    monkeypatch.setattr(mail_processing_module, "get_session", api_routes.get_session)
    monkeypatch.setattr(api_routes, "OllamaModelClient", lambda: MagicMock())

    response = client.post("/api/v1/runs")
    assert response.status_code == 202
    run_id = response.json()["run_id"]

    session = db()
    all_runs = session.query(ProcessingRun).all()
    assert len(all_runs) == 1
    assert all_runs[0].id == run_id
    assert all_runs[0].status == "completed"

    status_response = client.get("/api/v1/status")
    assert status_response.json()["run_in_progress"] is False


def test_start_run_rejects_arbitrary_query_or_label_fields(client, db, monkeypatch):
    from app.api import routes as api_routes

    monkeypatch.setattr(api_routes.threading, "Thread", lambda target, args, daemon: MagicMock())

    response = client.post("/api/v1/runs", json={"gmail_query": "in:anywhere", "label": "Anything"})
    assert response.status_code == 202  # body is ignored entirely, not validated as run params


def test_get_run_status(client, db):
    session = db()
    run = ProcessingRun(trigger_type=TriggerType.MANUAL, discovered_count=3, processed_count=2, skipped_count=1)
    session.add(run)
    session.commit()

    response = client.get(f"/api/v1/runs/{run.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["discovered_count"] == 3
    assert body["processed_count"] == 2


def test_get_run_not_found_returns_safe_404(client, db):
    response = client.get("/api/v1/runs/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "not_found"
    assert "Traceback" not in body["detail"]


def test_open_items_excludes_replied_request(client, db, monkeypatch):
    from app.api import routes as api_routes

    _seed_message_with_analysis(db, external_message_id="msg-1")
    gmail_mock = MagicMock()
    gmail_mock.has_been_replied_to.return_value = True
    monkeypatch.setattr(api_routes, "GmailRetrievalService", lambda: gmail_mock)

    response = client.get("/api/v1/open-items")

    assert response.status_code == 200
    assert response.json() == []


def test_open_items_includes_unreplied_request_with_nag_level(client, db, monkeypatch):
    from app.api import routes as api_routes

    _seed_message_with_analysis(
        db, external_message_id="msg-1", received_at=datetime.now(timezone.utc) - timedelta(hours=30)
    )
    gmail_mock = MagicMock()
    gmail_mock.has_been_replied_to.return_value = False
    monkeypatch.setattr(api_routes, "GmailRetrievalService", lambda: gmail_mock)

    response = client.get("/api/v1/open-items")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["nag_level"] == "persistent"
    gmail_mock.has_been_replied_to.assert_called_once()


def test_open_items_excludes_non_request_category(client, db, monkeypatch):
    from app.api import routes as api_routes

    _seed_message_with_analysis(db, category="information")
    gmail_mock = MagicMock()
    monkeypatch.setattr(api_routes, "GmailRetrievalService", lambda: gmail_mock)

    response = client.get("/api/v1/open-items")

    assert response.status_code == 200
    assert response.json() == []
    gmail_mock.has_been_replied_to.assert_not_called()


def test_list_summaries_includes_suggested_action(client, db):
    _seed_message_with_analysis(db)
    response = client.get("/api/v1/summaries")
    assert response.json()["items"][0]["suggested_action"] == "Review the report"


def test_list_summaries_basic_pagination(client, db):
    for i in range(3):
        _seed_message_with_analysis(db, external_message_id=f"msg-{i}", subject=f"Subject {i}")

    response = client.get("/api/v1/summaries", params={"limit": 2, "offset": 0})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["limit"] == 2
    assert body["offset"] == 0


def test_list_summaries_filters_by_priority_and_action_required(client, db):
    _seed_message_with_analysis(db, external_message_id="msg-a", priority="high", action_required=True)
    _seed_message_with_analysis(db, external_message_id="msg-b", priority="low", action_required=False)

    response = client.get("/api/v1/summaries", params={"priority": "high"})
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["priority"] == "high"

    response = client.get("/api/v1/summaries", params={"action_required": "false"})
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["action_required"] is False


def test_list_summaries_filters_by_category_and_date_range(client, db):
    _seed_message_with_analysis(
        db, external_message_id="msg-c", category="invoice", received_at=datetime(2025, 1, 1, tzinfo=timezone.utc)
    )
    _seed_message_with_analysis(
        db, external_message_id="msg-d", category="meeting", received_at=datetime(2025, 6, 1, tzinfo=timezone.utc)
    )

    response = client.get("/api/v1/summaries", params={"category": "invoice"})
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["category"] == "invoice"

    response = client.get(
        "/api/v1/summaries",
        params={"date_from": "2025-05-01T00:00:00Z", "date_to": "2025-12-31T00:00:00Z"},
    )
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["message_id"]


def test_list_summaries_rejects_invalid_filters(client, db):
    response = client.get("/api/v1/summaries", params={"priority": "not-a-priority"})
    assert response.status_code == 422

    response = client.get("/api/v1/summaries", params={"category": "not-a-category"})
    assert response.status_code == 422

    response = client.get("/api/v1/summaries", params={"limit": 0})
    assert response.status_code == 422

    response = client.get(
        "/api/v1/summaries", params={"date_from": "2025-12-31T00:00:00Z", "date_to": "2025-01-01T00:00:00Z"}
    )
    assert response.status_code == 422


def test_list_summaries_never_exposes_oauth_fields(client, db):
    _seed_message_with_analysis(db)
    response = client.get("/api/v1/summaries")
    body_text = response.text.lower()
    for forbidden in ("token", "credential", "oauth", "refresh_token", "access_token"):
        assert forbidden not in body_text


def test_get_summary_detail_excludes_full_email_body(client, db):
    message_id, summary_id = _seed_message_with_analysis(db)

    response = client.get(f"/api/v1/summaries/{summary_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["message_id"] == message_id
    assert body["summary"] == "Alice asks Bob to review the report."
    # the analysis summary is present, but no raw email body field exists at all
    assert "body" not in body
    assert "raw_content" not in body


def test_get_summary_not_found(client, db):
    response = client.get("/api/v1/summaries/does-not-exist")
    assert response.status_code == 404


def test_get_summary_includes_pending_review_by_default(client, db):
    _message_id, summary_id = _seed_message_with_analysis(db)

    response = client.get(f"/api/v1/summaries/{summary_id}")
    assert response.status_code == 200
    review = response.json()["review"]
    assert review["review_status"] == "pending"
    assert review["reviewed_at"] is None
    assert review["completed_at"] is None
    assert review["dismissed_at"] is None
    assert review["user_note"] is None


def test_list_summaries_includes_review_status(client, db):
    _seed_message_with_analysis(db)
    response = client.get("/api/v1/summaries")
    assert response.json()["items"][0]["review_status"] == "pending"


def test_mark_summary_reviewed(client, db):
    _message_id, summary_id = _seed_message_with_analysis(db)

    response = client.patch(f"/api/v1/summaries/{summary_id}/review", json={"review_status": "reviewed"})
    assert response.status_code == 200
    body = response.json()
    assert body["review_status"] == "reviewed"
    assert body["reviewed_at"] is not None


def test_mark_summary_completed(client, db):
    _message_id, summary_id = _seed_message_with_analysis(db)

    response = client.patch(f"/api/v1/summaries/{summary_id}/review", json={"review_status": "completed"})
    assert response.status_code == 200
    body = response.json()
    assert body["review_status"] == "completed"
    assert body["completed_at"] is not None


def test_dismiss_summary(client, db):
    _message_id, summary_id = _seed_message_with_analysis(db)

    response = client.patch(f"/api/v1/summaries/{summary_id}/review", json={"review_status": "dismissed"})
    assert response.status_code == 200
    body = response.json()
    assert body["review_status"] == "dismissed"
    assert body["dismissed_at"] is not None


def test_review_status_change_persists_across_requests(client, db):
    _message_id, summary_id = _seed_message_with_analysis(db)
    client.patch(f"/api/v1/summaries/{summary_id}/review", json={"review_status": "reviewed"})

    response = client.get(f"/api/v1/summaries/{summary_id}")
    assert response.json()["review"]["review_status"] == "reviewed"


def test_cannot_transition_out_of_completed(client, db):
    _message_id, summary_id = _seed_message_with_analysis(db)
    client.patch(f"/api/v1/summaries/{summary_id}/review", json={"review_status": "completed"})

    response = client.patch(f"/api/v1/summaries/{summary_id}/review", json={"review_status": "reviewed"})
    assert response.status_code == 409
    assert "exception" not in response.json()["detail"].lower()


def test_cannot_transition_out_of_dismissed(client, db):
    _message_id, summary_id = _seed_message_with_analysis(db)
    client.patch(f"/api/v1/summaries/{summary_id}/review", json={"review_status": "dismissed"})

    response = client.patch(f"/api/v1/summaries/{summary_id}/review", json={"review_status": "completed"})
    assert response.status_code == 409


def test_update_review_status_rejects_unknown_status_value(client, db):
    _message_id, summary_id = _seed_message_with_analysis(db)

    response = client.patch(f"/api/v1/summaries/{summary_id}/review", json={"review_status": "pending"})
    assert response.status_code == 422


def test_update_review_status_for_missing_summary_returns_404(client, db):
    response = client.patch("/api/v1/summaries/does-not-exist/review", json={"review_status": "reviewed"})
    assert response.status_code == 404


def test_set_review_note(client, db):
    _message_id, summary_id = _seed_message_with_analysis(db)

    response = client.put(f"/api/v1/summaries/{summary_id}/review/note", json={"user_note": "Follow up Monday"})
    assert response.status_code == 200
    assert response.json()["user_note"] == "Follow up Monday"

    detail = client.get(f"/api/v1/summaries/{summary_id}").json()
    assert detail["review"]["user_note"] == "Follow up Monday"


def test_setting_note_does_not_change_review_status(client, db):
    _message_id, summary_id = _seed_message_with_analysis(db)

    response = client.put(f"/api/v1/summaries/{summary_id}/review/note", json={"user_note": "just a note"})
    assert response.json()["review_status"] == "pending"


def test_note_can_be_cleared(client, db):
    _message_id, summary_id = _seed_message_with_analysis(db)
    client.put(f"/api/v1/summaries/{summary_id}/review/note", json={"user_note": "something"})

    response = client.put(f"/api/v1/summaries/{summary_id}/review/note", json={"user_note": None})
    assert response.json()["user_note"] is None


def test_review_status_change_never_calls_gmail(client, db, monkeypatch):
    from app.api import routes as api_routes

    _message_id, summary_id = _seed_message_with_analysis(db)
    gmail_mock = MagicMock()
    monkeypatch.setattr(api_routes, "GmailRetrievalService", lambda: gmail_mock)

    client.patch(f"/api/v1/summaries/{summary_id}/review", json={"review_status": "completed"})
    client.put(f"/api/v1/summaries/{summary_id}/review/note", json={"user_note": "note"})

    gmail_mock.assert_not_called()


def test_review_status_update_writes_audit_log(client, db):
    from app.database.models import ReviewAuditLog

    _message_id, summary_id = _seed_message_with_analysis(db)
    client.patch(f"/api/v1/summaries/{summary_id}/review", json={"review_status": "reviewed"})

    session = db()
    entries = session.query(ReviewAuditLog).filter_by(email_analysis_id=summary_id).all()
    assert len(entries) == 1
    assert entries[0].previous_status == "pending"
    assert entries[0].new_status == "reviewed"


def test_reprocess_reruns_analysis_without_touching_gmail(client, db, monkeypatch):
    from app.api import routes as api_routes

    _message_id, summary_id = _seed_message_with_analysis(db, external_message_id="msg-reprocess")

    document = EmailDocument(
        message_id="msg-reprocess",
        thread_id="thread-1",
        sender="alice@example.test",
        recipients=["bob@example.test"],
        subject="Report",
        date=datetime(2025, 7, 1, tzinfo=timezone.utc),
        body="Updated content for reprocessing.",
        truncated=False,
        quoted_history="",
        content_hash="b" * 64,
    )

    # GmailRetrievalService must never expose a write/modify method to call
    assert not hasattr(api_routes.GmailRetrievalService, "modify_message")
    gmail_mock = MagicMock()
    gmail_mock.get_message_for_analysis.return_value = document
    monkeypatch.setattr(api_routes, "GmailRetrievalService", lambda: gmail_mock)

    model_client_mock = MagicMock()
    model_client_mock.generate.return_value = (
        '{"summary": "Updated summary.", "priority": "low", "priority_reason": "r", '
        '"action_required": false, "suggested_action": null, "action_owner": null, '
        '"explicit_deadline": null, "deadline_text": null, "requires_reply": false, '
        '"category": "information", "uncertainty_notes": [], "confidence": 0.9}'
    )
    monkeypatch.setattr(api_routes, "OllamaModelClient", lambda: model_client_mock)

    response = client.post(f"/api/v1/summaries/{summary_id}/reprocess")
    assert response.status_code == 200
    body = response.json()
    assert body["model_name"] == "qwen3:1.7b"
    assert body["prompt_version"] == "v1"

    gmail_mock.get_message_for_analysis.assert_called_once_with("msg-reprocess")


def test_reprocess_returns_safe_error_on_model_failure(client, db, monkeypatch):
    from app.api import routes as api_routes

    _message_id, summary_id = _seed_message_with_analysis(db, external_message_id="msg-fail")

    document = EmailDocument(
        message_id="msg-fail",
        thread_id="thread-1",
        sender="alice@example.test",
        recipients=["bob@example.test"],
        subject="Report",
        date=datetime(2025, 7, 1, tzinfo=timezone.utc),
        body="content",
        truncated=False,
        quoted_history="",
        content_hash="c" * 64,
    )
    gmail_mock = MagicMock()
    gmail_mock.get_message_for_analysis.return_value = document
    monkeypatch.setattr(api_routes, "GmailRetrievalService", lambda: gmail_mock)

    def _raise(*_a, **_k):
        raise ModelUnavailableError("connection refused to internal host X")

    model_client_mock = MagicMock()
    model_client_mock.generate.side_effect = _raise
    monkeypatch.setattr(api_routes, "OllamaModelClient", lambda: model_client_mock)

    response = client.post(f"/api/v1/summaries/{summary_id}/reprocess")
    assert response.status_code == 502
    assert "connection refused" not in response.text
    assert "internal host" not in response.text


def test_unhandled_exception_never_leaks_internals(client, db, monkeypatch):
    from app.api import routes as api_routes

    def _boom(*_a, **_k):
        raise RuntimeError("secret database connection string leaked here")

    monkeypatch.setattr(api_routes, "_has_running_run", _boom)

    unsafe_client = TestClient(app, raise_server_exceptions=False)
    response = unsafe_client.post("/api/v1/runs")
    assert response.status_code == 500
    assert "secret" not in response.text
    assert "RuntimeError" not in response.text
    body = response.json()
    assert body["error"] == "internal_error"
