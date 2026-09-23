"""Orchestrates a full processing run: Gmail -> sanitize -> claim -> analyze -> store.

Never stores raw email bodies (only a content hash), never logs bodies/prompts/model
output/tokens (see app.logging_config's forbidden keys), never executes a suggested
action, and never calls a Gmail write/modify method (GmailRetrievalService exposes none).
"""
from __future__ import annotations

from datetime import datetime, timezone

import structlog

from app.agent.analysis import PROMPT_VERSION, AnalysisFailedError, analyze_email
from app.agent.model_client import ModelClient, ModelNotInstalledError, ModelUnavailableError
from app.config import settings
from app.database.models import EmailAnalysis, ProcessedMessage, ProcessingRun, RunStatus, TriggerType
from app.database.repository import EmailAnalysisRepository, ProcessedMessageRepository, ProcessingRunRepository
from app.database.session import get_session
from app.email.retrieval import GmailRetrievalService, MessageUnavailableError
from app.email.sanitization import SanitizedEmail

log = structlog.get_logger()


def _sanitized_email_from_document(document) -> SanitizedEmail:
    headers_text = (
        f"From: {document.sender}\n"
        f"To: {', '.join(document.recipients)}\n"
        f"Subject: {document.subject}\n"
        f"Date: {document.date.isoformat() if document.date else ''}"
    )
    return SanitizedEmail(
        headers_text=headers_text,
        current_message=document.body,
        quoted_history=document.quoted_history,
        truncated=document.truncated,
        content_hash=document.content_hash,
    )


class MailProcessingService:
    """One processing run over candidate Gmail messages, one message fully isolated
    from the next: a single message's failure is recorded and processing continues.
    """

    def __init__(self, model_client: ModelClient, gmail_service: GmailRetrievalService | None = None) -> None:
        self._model_client = model_client
        self._gmail = gmail_service or GmailRetrievalService()

    def run(self, trigger_type: TriggerType = TriggerType.MANUAL, run_id: str | None = None) -> ProcessingRun:
        """Runs one full pass. If run_id names an existing RUNNING row (e.g. one
        the caller already created and returned to a client), that row is used
        and updated in place; otherwise a new ProcessingRun row is created here.
        Passing an existing run_id avoids leaving the caller's row orphaned in
        RUNNING forever while this method tracks progress on a second row.
        """
        if run_id is None:
            with get_session() as session:
                run_repo = ProcessingRunRepository(session)
                run = run_repo.start(ProcessingRun(trigger_type=trigger_type))
                run_id = run.id
                session.commit()

        candidate_ids = self._gmail.list_candidate_message_ids()

        discovered = len(candidate_ids)
        processed = skipped = failed = 0

        for message_id in candidate_ids:
            with get_session() as session:
                message_repo = ProcessedMessageRepository(session)

                existing = message_repo.get_by_external_id("gmail", message_id)
                if existing is not None and existing.processing_status == "completed":
                    skipped += 1
                    continue

                outcome = self._process_one(session, run_id, message_id)
                if outcome == "processed":
                    processed += 1
                elif outcome == "skipped":
                    skipped += 1
                else:
                    failed += 1

        with get_session() as session:
            run_repo = ProcessingRunRepository(session)
            run = run_repo.get(run_id)
            run.discovered_count = discovered
            run.processed_count = processed
            run.skipped_count = skipped
            run.failed_count = failed
            run_repo.complete(run, datetime.now(timezone.utc), status=RunStatus.COMPLETED)
            return run

    def _process_one(self, session, run_id: str, message_id: str) -> str:
        """Process a single message end to end. Returns "processed", "skipped", or "failed".
        Never raises: any exception here is caught and recorded as a FAILED attempt so one
        bad message can't stop the run.
        """
        message_repo = ProcessedMessageRepository(session)
        analysis_repo = EmailAnalysisRepository(session)

        try:
            document = self._gmail.get_message_for_analysis(message_id)
            if document is None:
                return "skipped"

            message = message_repo.get_by_external_id("gmail", message_id)
            if message is None:
                message = message_repo.create(
                    ProcessedMessage(
                        provider="gmail",
                        external_message_id=message_id,
                        external_thread_id=document.thread_id,
                        received_at=document.date or datetime.now(timezone.utc),
                        sender_address=document.sender,
                        subject=document.subject,
                        content_hash=document.content_hash,
                    )
                )
                session.commit()

            if not message_repo.claim(message.id, claimed_by=run_id):
                return "skipped"
            session.commit()

            sanitized = _sanitized_email_from_document(document)

            try:
                result = analyze_email(sanitized, self._model_client)
            except (ModelUnavailableError, ModelNotInstalledError):
                message_repo.mark_failed(message, "MODEL_UNAVAILABLE")
                session.commit()
                return "failed"
            except AnalysisFailedError:
                message_repo.mark_failed(message, "ANALYSIS_VALIDATION_FAILED")
                session.commit()
                return "failed"

            analysis_repo.create(
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
            session.commit()
            return "processed"

        except MessageUnavailableError:
            return "skipped"
        except Exception:
            session.rollback()
            log.error("message_processing_failed", message_id=message_id)
            try:
                message = message_repo.get_by_external_id("gmail", message_id)
                if message is not None:
                    message_repo.mark_failed(message, "UNEXPECTED_ERROR")
                    session.commit()
            except Exception:
                session.rollback()
            return "failed"


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
