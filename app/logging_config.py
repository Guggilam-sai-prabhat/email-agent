import logging

import structlog

from app.config import settings

# Log event keys that must never be emitted, even if a caller passes them
# by mistake: email bodies, OAuth tokens, and model prompts are sensitive.
_FORBIDDEN_KEYS = {"email_body", "body", "token", "access_token", "refresh_token", "prompt"}


def _redact_forbidden_keys(_logger, _method_name, event_dict):
    for key in _FORBIDDEN_KEYS:
        if key in event_dict:
            event_dict[key] = "[REDACTED]"
    return event_dict


def configure_logging() -> None:
    logging.basicConfig(level=settings.log_level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            _redact_forbidden_keys,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(settings.log_level)),
        logger_factory=structlog.PrintLoggerFactory(),
    )
