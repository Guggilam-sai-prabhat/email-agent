import pytest

from app.config import Settings, load_settings


def test_defaults_are_safe(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    settings = Settings(_env_file=None)

    assert settings.app_host == "127.0.0.1"
    assert settings.ollama_base_url == "http://127.0.0.1:11434"
    assert settings.ollama_model == "qwen3:1.7b"
    assert settings.ollama_context_size == 8192
    assert settings.gmail_allowed_label == ""
    assert settings.gmail_unlabeled_message_limit == 10
    assert settings.email_max_age_days == 7
    assert settings.email_max_messages_per_run == 50
    assert settings.include_attachments is False
    assert settings.scheduler_enabled is False


def test_rejects_public_bind_in_production():
    with pytest.raises(ValueError):
        Settings(_env_file=None, app_env="production", app_host="0.0.0.0")


def test_allows_public_bind_with_explicit_override():
    settings = Settings(
        _env_file=None, app_env="production", app_host="0.0.0.0", allow_public_bind=True
    )
    assert settings.app_host == "0.0.0.0"


def test_rejects_non_localhost_ollama_in_production():
    with pytest.raises(ValueError):
        Settings(_env_file=None, app_env="production", ollama_base_url="http://example.com:11434")


def test_allows_non_localhost_ollama_with_explicit_override():
    settings = Settings(
        _env_file=None,
        app_env="production",
        ollama_base_url="http://example.com:11434",
        allow_public_bind=True,
    )
    assert settings.ollama_base_url == "http://example.com:11434"


def test_rejects_include_attachments_true():
    with pytest.raises(ValueError):
        Settings(_env_file=None, include_attachments=True)


@pytest.mark.parametrize(
    "field",
    [
        "app_port",
        "ollama_context_size",
        "email_max_age_days",
        "email_max_messages_per_run",
        "email_max_body_characters",
        "schedule_interval_minutes",
    ],
)
def test_rejects_non_positive_integers(field):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{field: 0})


def test_startup_validation_error_never_leaks_secret_values(monkeypatch):
    secret = "postgres://user:supersecret@host/db"  # noqa: S105 - test fixture, not a real credential
    monkeypatch.setenv("DATABASE_URL", secret)
    monkeypatch.setenv("APP_PORT", "not-an-integer")

    with pytest.raises(RuntimeError) as exc_info:
        load_settings()

    assert secret not in str(exc_info.value)
