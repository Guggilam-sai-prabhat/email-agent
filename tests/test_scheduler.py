from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from apscheduler.triggers.interval import IntervalTrigger
from google.auth.exceptions import RefreshError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import SingletonThreadPool

from app.database.models import Base, ProcessingRun, RunStatus, TriggerType
from app.scheduler import (
    ModelConcurrencyGate,
    _GatedModelClient,
    create_scheduler,
    run_scheduled_job,
)
from app.database import session as db_session


class FakeClock:
    """A controllable clock so trigger math can be tested without real sleeps."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def advance(self, **kwargs) -> datetime:
        from datetime import timedelta

        self.now += timedelta(**kwargs)
        return self.now


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=SingletonThreadPool,
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

    monkeypatch.setattr(db_session, "get_session", _fake_get_session)
    monkeypatch.setattr("app.scheduler.get_session", _fake_get_session)
    return TestSession


def test_scheduler_disabled_by_default():
    from app.config import settings

    assert settings.scheduler_enabled is False


def test_interval_trigger_uses_controllable_clock():
    """The interval trigger's next-fire computation is pure datetime math —
    drive it with a FakeClock instead of waiting on a real timer."""
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    trigger = IntervalTrigger(minutes=30, start_date=clock.now)

    first_fire = trigger.get_next_fire_time(None, clock.now)
    assert first_fire == clock.now

    clock.advance(minutes=30)
    second_fire = trigger.get_next_fire_time(first_fire, clock.now)
    assert second_fire == clock.now


def test_scheduler_job_prevents_overlap():
    scheduler = create_scheduler()
    scheduler.start(paused=True)
    job = scheduler.get_job("periodic_email_processing")
    assert job.max_instances == 1
    scheduler.shutdown(wait=False)


def test_scheduler_coalesces_missed_runs_after_restart():
    scheduler = create_scheduler()
    assert scheduler._job_defaults["coalesce"] is True
    assert scheduler._job_defaults["misfire_grace_time"] is None


def test_model_concurrency_gate_defaults_to_one():
    from app.scheduler import model_concurrency_gate

    assert model_concurrency_gate._semaphore._value == 1


def test_gated_model_client_serializes_calls():
    inner = MagicMock()
    inner.generate.return_value = "ok"
    gate = ModelConcurrencyGate(max_concurrency=1)
    assert gate._semaphore.acquire(blocking=False)
    gate._semaphore.release()

    client = _GatedModelClient(inner, gate)
    assert client.generate("prompt") == "ok"
    # Gate must be released after the call so a later call can proceed.
    assert gate._semaphore.acquire(blocking=False)
    gate._semaphore.release()


def test_run_scheduled_job_records_every_fire(db, monkeypatch):
    monkeypatch.setattr("app.scheduler.OllamaModelClient", MagicMock())

    fake_service = MagicMock()
    monkeypatch.setattr("app.scheduler.MailProcessingService", MagicMock(return_value=fake_service))

    run_scheduled_job()

    with db() as session:
        runs = session.query(ProcessingRun).all()
        assert len(runs) == 1
        assert runs[0].trigger_type == TriggerType.SCHEDULED
        assert runs[0].status == RunStatus.RUNNING  # left to service.run() to complete; here it's a no-op mock


def test_run_scheduled_job_retries_transient_error_then_succeeds(db, monkeypatch):
    monkeypatch.setattr("app.scheduler.OllamaModelClient", MagicMock())
    monkeypatch.setattr("app.scheduler.time.sleep", lambda _seconds: None)

    attempts = {"count": 0}

    def _flaky_run(*, trigger_type, run_id):
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise ConnectionError("transient network blip")

    fake_service = MagicMock()
    fake_service.run.side_effect = _flaky_run
    monkeypatch.setattr("app.scheduler.MailProcessingService", MagicMock(return_value=fake_service))

    run_scheduled_job()

    assert attempts["count"] == 2


def test_run_scheduled_job_does_not_retry_permanent_oauth_error(db, monkeypatch):
    monkeypatch.setattr("app.scheduler.OllamaModelClient", MagicMock())
    monkeypatch.setattr("app.scheduler.time.sleep", lambda _seconds: (_ for _ in ()).throw(AssertionError("must not sleep/retry on a permanent error")))

    attempts = {"count": 0}

    def _always_fails(*, trigger_type, run_id):
        attempts["count"] += 1
        raise RefreshError("invalid_grant: token revoked")

    fake_service = MagicMock()
    fake_service.run.side_effect = _always_fails
    monkeypatch.setattr("app.scheduler.MailProcessingService", MagicMock(return_value=fake_service))

    run_scheduled_job()

    assert attempts["count"] == 1

    with db() as session:
        run = session.query(ProcessingRun).one()
        assert run.status == RunStatus.FAILED
        assert "RefreshError" in run.error_summary


def test_run_scheduled_job_gives_up_after_max_retries(db, monkeypatch):
    monkeypatch.setattr("app.scheduler.OllamaModelClient", MagicMock())
    monkeypatch.setattr("app.scheduler.time.sleep", lambda _seconds: None)

    fake_service = MagicMock()
    fake_service.run.side_effect = ConnectionError("still down")
    monkeypatch.setattr("app.scheduler.MailProcessingService", MagicMock(return_value=fake_service))

    run_scheduled_job()

    from app.scheduler import _MAX_RETRIES

    assert fake_service.run.call_count == _MAX_RETRIES + 1

    with db() as session:
        run = session.query(ProcessingRun).one()
        assert run.status == RunStatus.FAILED
        assert "retries exhausted" in run.error_summary
