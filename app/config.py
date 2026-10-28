from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment variables / .env."""

    model_config = SettingsConfigDict(env_file=".env")

    app_name: str = "mail-agent"
    app_env: str = "development"
    log_level: str = "INFO"

    # Bind to localhost only by default — this agent must never be exposed
    # on the network without an explicit operator decision.
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    allow_public_bind: bool = False

    database_url: str = "sqlite:///./mail_agent.db"

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:1.7b"
    ollama_context_size: int = 8192

    gmail_credentials_file: str = "credentials.json"
    gmail_token_file: str = "token.json"
    # Empty/unset means "no label filter" — the agent falls back to the most
    # recent inbox messages (see gmail_unlabeled_message_limit) instead of
    # requiring the operator to create and apply a specific Gmail label first.
    gmail_allowed_label: str = ""
    # Cap used only when gmail_allowed_label is unset, since "most recent
    # inbox messages" has no label to bound it the way a labeled query does.
    gmail_unlabeled_message_limit: int = 10
    # Per-request socket timeout for Gmail API calls. Without this, a stalled
    # connection can block a processing run's background thread forever.
    gmail_request_timeout_seconds: int = 30

    email_max_age_days: int = 7
    email_max_messages_per_run: int = 50
    email_max_body_characters: int = 5000
    include_attachments: bool = False

    scheduler_enabled: bool = False
    schedule_interval_minutes: int = 30
    # Qwen3 1.7B runs locally in Ollama; concurrent inference just contends for
    # the same process, so this defaults to 1, not "however many jobs fire".
    model_max_concurrency: int = 1

    # Age thresholds (hours) for an open request item's nag_level: below
    # nag_noticeable_after_hours is "quiet", below nag_persistent_after_hours
    # is "noticeable", at/above it is "persistent".
    nag_noticeable_after_hours: int = 4
    nag_persistent_after_hours: int = 24

    max_processing_attempts: int = 3
    # How long a message may stay claimed (PROCESSING) before another worker may
    # treat the claim as stale (e.g. after a crash) and reclaim it.
    processing_timeout_seconds: int = 600
    # How long a whole run may stay RUNNING before the API treats it as stuck
    # (crashed thread, hung network call) and marks it FAILED.
    run_timeout_seconds: int = 900

    @field_validator(
        "app_port",
        "ollama_context_size",
        "email_max_age_days",
        "email_max_messages_per_run",
        "email_max_body_characters",
        "schedule_interval_minutes",
        "max_processing_attempts",
        "processing_timeout_seconds",
        "run_timeout_seconds",
        "gmail_request_timeout_seconds",
        "gmail_unlabeled_message_limit",
        "model_max_concurrency",
        "nag_noticeable_after_hours",
        "nag_persistent_after_hours",
    )
    @classmethod
    def _must_be_positive(cls, value: int, info) -> int:
        if value <= 0:
            raise ValueError(f"{info.field_name} must be a positive integer")
        return value

    @model_validator(mode="after")
    def _nag_thresholds_ordered(self) -> "Settings":
        if self.nag_persistent_after_hours <= self.nag_noticeable_after_hours:
            raise ValueError("NAG_PERSISTENT_AFTER_HOURS must be greater than NAG_NOTICEABLE_AFTER_HOURS")
        return self

    @field_validator("include_attachments")
    @classmethod
    def _no_attachments_in_mvp(cls, value: bool) -> bool:
        if value:
            raise ValueError("INCLUDE_ATTACHMENTS is not supported in the MVP; it must be false")
        return value

    @model_validator(mode="after")
    def _no_public_bind_in_production(self) -> "Settings":
        if self.app_env == "production" and self.app_host == "0.0.0.0" and not self.allow_public_bind:
            raise ValueError(
                "APP_HOST=0.0.0.0 is not allowed in production unless ALLOW_PUBLIC_BIND=true is set explicitly"
            )
        return self

    @model_validator(mode="after")
    def _ollama_localhost_by_default(self) -> "Settings":
        allowed_hosts = ("127.0.0.1", "localhost")
        if self.app_env != "production" or self.allow_public_bind:
            return self
        host = self.ollama_base_url.split("://", 1)[-1].split(":", 1)[0].split("/", 1)[0]
        if host not in allowed_hosts:
            raise ValueError(
                "OLLAMA_BASE_URL must point to localhost in production unless ALLOW_PUBLIC_BIND=true is set explicitly"
            )
        return self


def load_settings() -> Settings:
    """Load and validate settings at startup, failing fast on misconfiguration."""
    try:
        return Settings()
    except Exception as exc:
        # Never leak env var values (may contain secrets) into the error surface.
        raise RuntimeError(f"Invalid configuration: {type(exc).__name__}") from exc


settings = load_settings()
