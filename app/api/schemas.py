"""Response/request schemas for the manual API. Never include OAuth tokens,
raw email bodies, or exception internals — see app/api/routes.py."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.database.models import Priority, ReviewStatus, RunStatus
from app.services.open_items import NagLevel

Category = Literal["information", "request", "approval", "meeting", "alert", "invoice", "other"]


class RunStartResponse(BaseModel):
    run_id: str
    status: RunStatus


class RunStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: RunStatus
    started_at: datetime
    completed_at: datetime | None
    discovered_count: int
    processed_count: int
    skipped_count: int
    failed_count: int


class SummaryListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    summary_id: str = Field(validation_alias="id")
    message_id: str = Field(validation_alias="processed_message_id")
    subject: str
    sender_address: str
    received_at: datetime
    priority: Priority
    category: Category | None = None
    action_required: bool
    suggested_action: str | None = None
    confidence: float
    created_at: datetime
    review_status: ReviewStatus = ReviewStatus.PENDING


class ReviewInfo(BaseModel):
    review_status: ReviewStatus
    reviewed_at: datetime | None
    completed_at: datetime | None
    dismissed_at: datetime | None
    user_note: str | None


class SummaryDetail(BaseModel):
    summary_id: str
    message_id: str
    subject: str
    sender_address: str
    received_at: datetime
    priority: Priority
    priority_reason: str | None
    action_required: bool
    suggested_action: str | None
    explicit_deadline: datetime | None
    confidence: float
    model_name: str
    prompt_version: str
    created_at: datetime
    summary: str
    review: ReviewInfo


class SummaryPage(BaseModel):
    items: list[SummaryListItem]
    total: int
    limit: int
    offset: int


class SummaryFilters(BaseModel):
    priority: Priority | None = None
    action_required: bool | None = None
    category: Category | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    limit: int = Field(default=20, ge=1, le=200)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _date_range_valid(self) -> "SummaryFilters":
        if self.date_from is not None and self.date_to is not None and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self


class AgentStatusResponse(BaseModel):
    model_available: bool
    model_name: str
    gmail_authorized: bool
    run_in_progress: bool
    last_successful_run_at: datetime | None


class ReviewStatusUpdateRequest(BaseModel):
    review_status: Literal["reviewed", "completed", "dismissed"]


class ReviewNoteUpdateRequest(BaseModel):
    user_note: str | None = Field(default=None, max_length=4000)


class ReprocessResponse(BaseModel):
    message_id: str
    status: Literal["completed"]
    model_name: str
    prompt_version: str


class ErrorResponse(BaseModel):
    error: str
    detail: str


class BriefItemResponse(BaseModel):
    message_id: str
    subject: str
    summary: str
    priority: Priority
    category: Category | None = None
    explicit_deadline: datetime | None
    is_overdue: bool


class DailyBriefResponse(BaseModel):
    """Local-dashboard-only view over already-validated analyses. Never sent by
    email or pushed as a notification. `suggested_order` is a deterministic
    ordering fact (priority + deadline), not a model recommendation."""

    generated_at: datetime
    urgent: list[BriefItemResponse]
    deadlines: list[BriefItemResponse]
    needs_reply: list[BriefItemResponse]
    other_action_items: list[BriefItemResponse]
    informational: list[BriefItemResponse]
    suggested_order: list[BriefItemResponse]


class OpenItemResponse(BaseModel):
    message_id: str
    subject: str
    sender_address: str
    received_at: datetime
    age_hours: float
    nag_level: NagLevel
