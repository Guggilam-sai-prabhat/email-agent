from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.routes import (
    MessageNotFoundError,
    ReprocessFailedError,
    ReviewTransitionError,
    RunAlreadyInProgressError,
    router,
)
from app.auth.gmail_auth import validate_stored_token_scopes
from app.config import settings
from app.logging_config import configure_logging
from app.scheduler import shutdown_scheduler, start_scheduler

configure_logging()
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_stored_token_scopes()
    log.info("app_startup", environment=settings.app_env)
    start_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
    description=(
        "Local-only manual operator API for the mail agent. No authentication is "
        "implemented — this API must never be exposed beyond localhost/127.0.0.1. "
        "See README.md for the bind-address guarantees."
    ),
)

# Lets the desktop companion's webview (served from http://localhost:1420 in
# dev, tauri://localhost in a packaged build) call this localhost-only API.
# No authentication exists either way, so this doesn't widen what's exposed.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(tauri://localhost|https?://(localhost|127\.0\.0\.1)(:\d+)?)$",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(RunAlreadyInProgressError)
def _run_in_progress_handler(_request: Request, _exc: RunAlreadyInProgressError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"error": "run_in_progress", "detail": "a processing run is already in progress"})


@app.exception_handler(MessageNotFoundError)
def _not_found_handler(_request: Request, _exc: MessageNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": "not_found", "detail": "the requested resource was not found"})


@app.exception_handler(ValidationError)
def _validation_error_handler(_request: Request, exc: ValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": "invalid_request", "detail": str(exc.errors())})


@app.exception_handler(ReviewTransitionError)
def _review_transition_handler(_request: Request, exc: ReviewTransitionError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"error": "invalid_review_transition", "detail": str(exc)})


@app.exception_handler(ReprocessFailedError)
def _reprocess_failed_handler(_request: Request, _exc: ReprocessFailedError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"error": "reprocess_failed", "detail": "analysis could not be completed"})


@app.exception_handler(Exception)
def _unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    log.error("unhandled_api_exception", exception_type=type(exc).__name__)
    return JSONResponse(status_code=500, content={"error": "internal_error", "detail": "an unexpected error occurred"})
