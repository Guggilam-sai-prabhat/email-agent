"""Single shared-secret auth for /api/v1: an X-API-Key header checked against a
key generated on first use and kept in a 0600 file (settings.api_key_file)."""
import hmac
import os
import secrets
from functools import lru_cache
from pathlib import Path

from fastapi import Header, HTTPException

from app.config import settings


@lru_cache
def get_api_key() -> str:
    path = Path(settings.api_key_file)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    with os.fdopen(fd, "w") as f:
        key = secrets.token_urlsafe(32)
        f.write(key)
    return key


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if x_api_key is None or not hmac.compare_digest(x_api_key, get_api_key()):
        raise HTTPException(status_code=401, detail="invalid or missing API key")
