"""Periodic processing via APScheduler.

Disabled by default (settings.scheduler_enabled). One job, one thread pool slot
(`max_instances=1`) so APScheduler itself refuses to start an overlapping run
instead of us hand-rolling a lock — this is the same guarantee
`_has_running_run`/`ProcessedMessage.claim` already give per-message, just
applied at the job level too as a second line of defense. A watchdog thread
enforces `run_timeout_seconds` as a hard wall-clock cap on the run. Every fire
is recorded as a ProcessingRun row (trigger_type=SCHEDULED) whether it
succeeds, times out, or errors — see MailProcessingService.run(), which
already creates/updates that row.

Never runs model inference concurrently with itself: OllamaModelClient calls
are serialized by ModelConcurrencyGate below, defaulting to 1 (Qwen3 1.7B is a
single local process; a second concurrent inference would just thrash it).
"""
from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timezone

import structlog
from apscheduler.executors.pool import ThreadPoolExecutor as APSThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from google.auth.exceptions import RefreshError

from app.agent.model_client import ModelClient, ModelNotInstalledError, ModelUnavailableError, OllamaModelClient
from app.auth.gmail_auth import ScopeViolationError
from app.config import settings
from app.database.models import ProcessingRun, RunStatus, TriggerType
from app.database.session import get_session
from app.services.mail_processing import MailProcessingService

log = structlog.get_logger()

JOB_ID = "periodic_email_processing"

# Permanent errors: retrying won't help without operator intervention (bad
# OAuth token, misconfigured host/model). Anything else transient
# (ModelUnavailableError, network errors, ModelNotInstalledError while Ollama
# is still warming up) gets bounded retry.
_PERMANENT_ERRORS = (RefreshError, ScopeViolationError, ValueError)

_MAX_RETRIES = 3
_BASE_BACKOFF_SECONDS = 2.0
_MAX_BACKOFF_SECONDS = 30.0


class ModelConcurrencyGate:
    """Bounds how many model inferences may run at once, process-wide.

    Default is 1: Qwen3 1.7B runs locally in Ollama and has no benefit from
    (and pays a real cost for) concurrent requests contending for the same
    process. Configurable only because the model client itself doesn't own
    concurrency — this gate is the single choke point regardless of who calls
    OllamaModelClient (scheduled job, manual run, reprocess endpoint).
    """

    def __init__(self, max_concurrency: int = 1) -> None:
        self._semaphore = threading.Semaphore(max_concurrency)

    def acquire(self) -> None:
        self._semaphore.acquire()

    def release(self) -> None:
        self._semaphore.release()


model_concurrency_gate = ModelConcurrencyGate(settings.model_max_concurrency)


class _GatedModelClient:
    """Wraps a ModelClient so every generate() call goes through the shared gate."""

    def __init__(self, inner: ModelClient, gate: ModelConcurrencyGate) -> None:
        self._inner = inner
        self._gate = gate

    def is_ready(self) -> bool:
        return self._inner.is_ready()

    def generate(self, prompt: str, format: dict | None = None) -> str:
        self._gate.acquire()
        try:
            return self._inner.generate(prompt, format=format)
        finally:
            self._gate.release()


def _record_scheduled_failure(run_id: str, error_summary: str) -> None:
    with get_session() as session:
        run = session.get(ProcessingRun, run_id)
        if run is not None and run.status == RunStatus.RUNNING:
            run.status = RunStatus.FAILED
            run.completed_at = datetime.now(timezone.utc)
            run.error_summary = error_summary[:500]


def run_scheduled_job() -> None:
    """One scheduled fire: create the ProcessingRun row up front (so every
    fire is recorded even if it never gets past retry), then attempt the run
    with bounded retry+backoff for transient errors, under a hard timeout.
    """
    with get_session() as session:
        run = ProcessingRun(trigger_type=TriggerType.SCHEDULED)
        session.add(run)
        session.flush()
        run_id = run.id

    model_client = _GatedModelClient(OllamaModelClient(), model_concurrency_gate)
    service = MailProcessingService(model_client=model_client)

    result: dict[str, BaseException] = {}

    def _target() -> None:
        try:
            service.run(trigger_type=TriggerType.SCHEDULED, run_id=run_id)
        except BaseException as exc:  # noqa: BLE001 - surfaced to the watchdog, not swallowed
            result["error"] = exc

    for attempt in range(_MAX_RETRIES + 1):
        result.clear()
        worker = threading.Thread(target=_target, daemon=True)
        worker.start()
        worker.join(timeout=settings.run_timeout_seconds)

        if worker.is_alive():
            # Hard timeout: the run row is left to _reap_stale_runs (routes.py) /
            # the next scheduled fire's staleness check, since we cannot safely
            # kill the worker thread mid-network-call. Record what we can here.
            log.error("scheduled_run_timed_out", run_id=run_id, attempt=attempt)
            _record_scheduled_failure(run_id, "scheduled run exceeded run_timeout_seconds")
            return

        error = result.get("error")
        if error is None:
            return

        if isinstance(error, _PERMANENT_ERRORS):
            log.error("scheduled_run_permanent_error", run_id=run_id, error_type=type(error).__name__)
            _record_scheduled_failure(run_id, f"permanent error: {type(error).__name__}")
            return

        if attempt >= _MAX_RETRIES:
            log.error("scheduled_run_retries_exhausted", run_id=run_id, error_type=type(error).__name__)
            _record_scheduled_failure(run_id, f"retries exhausted: {type(error).__name__}")
            return

        backoff = min(_BASE_BACKOFF_SECONDS * (2**attempt), _MAX_BACKOFF_SECONDS)
        backoff += random.uniform(0, backoff * 0.25)
        log.warning(
            "scheduled_run_transient_error_retrying",
            run_id=run_id,
            attempt=attempt,
            error_type=type(error).__name__,
            backoff_seconds=round(backoff, 2),
        )
        time.sleep(backoff)


def create_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(
        executors={"default": APSThreadPoolExecutor(max_workers=1)},
        job_defaults={
            "coalesce": True,  # after a restart/downtime, run once, not once per missed interval
            "misfire_grace_time": None,  # a missed fire always still runs — safe recovery after restart
            "max_instances": 1,  # belt-and-suspenders: refuse an overlapping run at the scheduler level
        },
    )
    scheduler.add_job(
        run_scheduled_job,
        trigger="interval",
        minutes=settings.schedule_interval_minutes,
        id=JOB_ID,
    )
    return scheduler


_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> BackgroundScheduler | None:
    """No-op if scheduler_enabled is False. Call shutdown_scheduler() on app shutdown."""
    global _scheduler
    if not settings.scheduler_enabled:
        log.info("scheduler_disabled")
        return None
    _scheduler = create_scheduler()
    _scheduler.start()
    log.info("scheduler_started", interval_minutes=settings.schedule_interval_minutes)
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=True)
        _scheduler = None
