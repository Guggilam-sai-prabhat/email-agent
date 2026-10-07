"""Manual operator API: start/inspect processing runs, browse and reprocess
summaries. Local-only surface — see app/main.py for the bind-address guard
and app/config.py for ALLOW_PUBLIC_BIND. No authentication (see README).
"""
from __future__ import annotations

import dataclasses
import threading
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.analysis import PROMPT_VERSION, AnalysisFailedError, analyze_email
from app.agent.model_client import ModelNotInstalledError, ModelUnavailableError, OllamaModelClient
from app.api.deps import get_db
from app.api.schemas import (
    AgentStatusResponse,
    DailyBriefResponse,
    ReprocessResponse,
    ReviewInfo,
    ReviewNoteUpdateRequest,
    ReviewStatusUpdateRequest,
    RunStartResponse,
    RunStatusResponse,
    SummaryDetail,
    SummaryFilters,
    SummaryPage,
)
from app.auth.gmail_auth import has_stored_token
from app.config import settings
from app.database.models import EmailAnalysis, ProcessedMessage, ProcessingRun, ReviewStatus, RunStatus, TriggerType
from app.database.repository import (
    EmailAnalysisRepository,
    EmailReviewRepository,
    InvalidReviewTransitionError,
    ProcessedMessageRepository,
)
from app.database.session import get_session
from app.email.retrieval import GmailRetrievalService, MessageUnavailableError
from app.services.daily_brief import build_daily_brief
from app.services.mail_processing import MailProcessingService, _sanitized_email_from_document

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1")


class RunAlreadyInProgressError(Exception):
    pass


class MessageNotFoundError(Exception):
    pass


def _reap_stale_runs(db: Session) -> None:
    """A run stuck in RUNNING past its timeout (crashed thread, hung network
    call) is marked FAILED rather than blocking the UI forever. Mirrors the
    message-claim staleness pattern in ProcessedMessageRepository.claim.
    """
    stale_cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.run_timeout_seconds)
    stmt = select(ProcessingRun).where(
        ProcessingRun.status == RunStatus.RUNNING, ProcessingRun.started_at < stale_cutoff
    )
    for run in db.execute(stmt).scalars().all():
        run.status = RunStatus.FAILED
        run.completed_at = datetime.now(timezone.utc)
        run.error_summary = "run exceeded timeout and was marked failed"
        log.error("stale_run_reaped", run_id=run.id)
    db.commit()


def _has_running_run(db: Session) -> bool:
    _reap_stale_runs(db)
    stmt = select(ProcessingRun.id).where(ProcessingRun.status == RunStatus.RUNNING).limit(1)
    return db.execute(stmt).first() is not None


def _run_in_background(run_id: str) -> None:
    """Executes the full run synchronously on a worker thread; the HTTP call
    already returned the run_id. Any unhandled failure is recorded on the run
    row rather than crashing the thread silently."""
    try:
        MailProcessingService(model_client=OllamaModelClient()).run(trigger_type=TriggerType.MANUAL, run_id=run_id)
    except Exception:
        log.error("background_run_failed", run_id=run_id)
        with get_session() as session:
            run = session.get(ProcessingRun, run_id)
            if run is not None and run.status == RunStatus.RUNNING:
                run.status = RunStatus.FAILED
                run.completed_at = datetime.now(timezone.utc)


@router.get("/status", response_model=AgentStatusResponse)
def get_status(db: Session = Depends(get_db)) -> AgentStatusResponse:
    last_run_stmt = (
        select(ProcessingRun.completed_at)
        .where(ProcessingRun.status == RunStatus.COMPLETED)
        .order_by(ProcessingRun.completed_at.desc())
        .limit(1)
    )
    last_successful_run_at = db.execute(last_run_stmt).scalar_one_or_none()

    return AgentStatusResponse(
        model_available=OllamaModelClient().is_ready(),
        model_name=settings.ollama_model,
        gmail_authorized=has_stored_token(),
        run_in_progress=_has_running_run(db),
        last_successful_run_at=last_successful_run_at,
    )


@router.post("/runs", response_model=RunStartResponse, status_code=202)
def start_run(db: Session = Depends(get_db)) -> RunStartResponse:
    """Starts one manual processing run against this operator's own configured
    Gmail label/query (app/config.py) — the request body cannot override
    what is fetched."""
    if _has_running_run(db):
        raise RunAlreadyInProgressError("a processing run is already in progress")

    run = ProcessingRun(trigger_type=TriggerType.MANUAL)
    db.add(run)
    db.commit()
    db.refresh(run)

    thread = threading.Thread(target=_run_in_background, args=(run.id,), daemon=True)
    thread.start()

    return RunStartResponse(run_id=run.id, status=run.status)


@router.get("/runs/{run_id}", response_model=RunStatusResponse)
def get_run(run_id: str, db: Session = Depends(get_db)) -> ProcessingRun:
    run = db.get(ProcessingRun, run_id)
    if run is None:
        raise MessageNotFoundError(f"run {run_id} not found")
    return run


@router.get("/summaries", response_model=SummaryPage)
def list_summaries(
    db: Session = Depends(get_db),
    filters: SummaryFilters = Depends(),
) -> SummaryPage:
    conditions = []
    if filters.priority is not None:
        conditions.append(EmailAnalysis.priority == filters.priority)
    if filters.action_required is not None:
        conditions.append(EmailAnalysis.action_required == filters.action_required)
    if filters.category is not None:
        conditions.append(EmailAnalysis.category == filters.category)
    if filters.date_from is not None:
        conditions.append(ProcessedMessage.received_at >= filters.date_from)
    if filters.date_to is not None:
        conditions.append(ProcessedMessage.received_at <= filters.date_to)

    base_stmt = select(EmailAnalysis).join(ProcessedMessage).where(*conditions)
    total_count = len(db.execute(base_stmt).scalars().all())

    rows = (
        db.execute(base_stmt.order_by(EmailAnalysis.created_at.desc()).offset(filters.offset).limit(filters.limit))
        .scalars()
        .all()
    )

    items = []
    for analysis in rows:
        message = analysis.processed_message
        items.append(
            {
                "id": analysis.id,
                "processed_message_id": analysis.processed_message_id,
                "subject": message.subject,
                "sender_address": message.sender_address,
                "received_at": message.received_at,
                "priority": analysis.priority,
                "category": analysis.category,
                "action_required": analysis.action_required,
                "suggested_action": analysis.suggested_action,
                "confidence": analysis.confidence,
                "created_at": analysis.created_at,
                "review_status": analysis.review.review_status if analysis.review else ReviewStatus.PENDING,
            }
        )

    return SummaryPage(items=items, total=total_count, limit=filters.limit, offset=filters.offset)


@router.get("/daily-brief", response_model=DailyBriefResponse)
def get_daily_brief(db: Session = Depends(get_db)) -> DailyBriefResponse:
    """Local-dashboard-only aggregation of validated analyses. Never emailed
    or pushed as a notification (see app/services/daily_brief.py)."""
    analyses = db.execute(select(EmailAnalysis).join(ProcessedMessage)).scalars().all()
    brief = build_daily_brief(list(analyses))
    return DailyBriefResponse(**dataclasses.asdict(brief))


@router.get("/summaries/{summary_id}", response_model=SummaryDetail)
def get_summary(summary_id: str, db: Session = Depends(get_db)) -> SummaryDetail:
    analysis = db.get(EmailAnalysis, summary_id)
    if analysis is None:
        raise MessageNotFoundError(f"summary {summary_id} not found")
    message = analysis.processed_message
    review = analysis.review
    return SummaryDetail(
        summary_id=analysis.id,
        message_id=message.id,
        subject=message.subject,
        sender_address=message.sender_address,
        received_at=message.received_at,
        priority=analysis.priority,
        priority_reason=analysis.priority_reason,
        action_required=analysis.action_required,
        suggested_action=analysis.suggested_action,
        explicit_deadline=analysis.explicit_deadline,
        confidence=analysis.confidence,
        model_name=analysis.model_name,
        prompt_version=analysis.prompt_version,
        created_at=analysis.created_at,
        summary=analysis.summary,
        review=ReviewInfo(
            review_status=review.review_status if review else ReviewStatus.PENDING,
            reviewed_at=review.reviewed_at if review else None,
            completed_at=review.completed_at if review else None,
            dismissed_at=review.dismissed_at if review else None,
            user_note=review.user_note if review else None,
        ),
    )


@router.patch("/summaries/{summary_id}/review", response_model=ReviewInfo)
def update_review_status(
    summary_id: str, body: ReviewStatusUpdateRequest, db: Session = Depends(get_db)
) -> ReviewInfo:
    """Records the user's review decision locally. Never touches Gmail — see
    EmailReview/ReviewAuditLog in app/database/models.py. The requested status
    must be a valid transition from the review's current status.
    """
    analysis = db.get(EmailAnalysis, summary_id)
    if analysis is None:
        raise MessageNotFoundError(f"summary {summary_id} not found")

    review_repo = EmailReviewRepository(db)
    review = review_repo.get_or_create(analysis.id)
    try:
        review_repo.set_status(review, ReviewStatus(body.review_status))
    except InvalidReviewTransitionError as exc:
        raise ReviewTransitionError(str(exc)) from exc
    db.commit()

    return ReviewInfo(
        review_status=review.review_status,
        reviewed_at=review.reviewed_at,
        completed_at=review.completed_at,
        dismissed_at=review.dismissed_at,
        user_note=review.user_note,
    )


@router.put("/summaries/{summary_id}/review/note", response_model=ReviewInfo)
def update_review_note(summary_id: str, body: ReviewNoteUpdateRequest, db: Session = Depends(get_db)) -> ReviewInfo:
    """Sets the private local note. Local-only, never sent to Gmail."""
    analysis = db.get(EmailAnalysis, summary_id)
    if analysis is None:
        raise MessageNotFoundError(f"summary {summary_id} not found")

    review_repo = EmailReviewRepository(db)
    review = review_repo.get_or_create(analysis.id)
    review_repo.set_note(review, body.user_note)
    db.commit()

    return ReviewInfo(
        review_status=review.review_status,
        reviewed_at=review.reviewed_at,
        completed_at=review.completed_at,
        dismissed_at=review.dismissed_at,
        user_note=review.user_note,
    )


class ReviewTransitionError(Exception):
    pass


@router.post("/summaries/{summary_id}/reprocess", response_model=ReprocessResponse)
def reprocess_summary(summary_id: str, db: Session = Depends(get_db)) -> ReprocessResponse:
    """Re-runs analysis for one message using a fresh, read-only fetch of its
    current Gmail content. Never calls a Gmail write/modify method."""
    analysis = db.get(EmailAnalysis, summary_id)
    if analysis is None:
        raise MessageNotFoundError(f"summary {summary_id} not found")

    message = analysis.processed_message
    message_repo = ProcessedMessageRepository(db)
    analysis_repo = EmailAnalysisRepository(db)
    gmail = GmailRetrievalService()

    try:
        document = gmail.get_message_for_analysis(message.external_message_id)
    except MessageUnavailableError:
        document = None
    if document is None:
        raise MessageNotFoundError(f"message {message.external_message_id} is no longer available in Gmail")

    sanitized = _sanitized_email_from_document(document)
    model_client = OllamaModelClient()

    try:
        result = analyze_email(sanitized, model_client)
    except (ModelUnavailableError, ModelNotInstalledError, AnalysisFailedError) as exc:
        raise ReprocessFailedError(str(type(exc).__name__)) from exc

    db.delete(analysis)
    db.flush()
    message_repo.reprocess(message)
    message_repo.claim(message.id, claimed_by="api-reprocess")
    new_analysis = analysis_repo.create(
        EmailAnalysis(
            processed_message_id=message.id,
            summary=result.summary,
            priority=result.priority,
            priority_reason=result.priority_reason,
            action_required=result.action_required,
            suggested_action=result.suggested_action,
            category=result.category,
            explicit_deadline=_parse_deadline(result.explicit_deadline),
            confidence=result.confidence,
            model_name=settings.ollama_model,
            prompt_version=PROMPT_VERSION,
        )
    )
    message_repo.mark_completed(message, datetime.now(timezone.utc))
    db.commit()

    return ReprocessResponse(
        message_id=message.id,
        status="completed",
        model_name=new_analysis.model_name,
        prompt_version=new_analysis.prompt_version,
    )


class ReprocessFailedError(Exception):
    pass


def _parse_deadline(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
