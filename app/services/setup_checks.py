"""First-run readiness checks shown by the companion's setup wizard.

Only inspects the machine (disk, Ollama, model, credential files); never reads mail.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from app.agent.model_client import ModelNotInstalledError, ModelUnavailableError, OllamaModelClient
from app.auth.gmail_auth import has_stored_token
from app.config import settings

MIN_FREE_GB = 5  # qwen3:1.7b is ~1.4 GB; the rest is headroom for the DB and Ollama


@dataclass(frozen=True)
class Check:
    id: str
    label: str
    ok: bool
    detail: str
    fix: str | None = None


def run_checks() -> list[Check]:
    free_gb = shutil.disk_usage(Path.home()).free / 1e9
    checks = [
        Check(
            "disk", f"{MIN_FREE_GB} GB free disk space", free_gb >= MIN_FREE_GB, f"{free_gb:.1f} GB free",
            f"Free up at least {MIN_FREE_GB} GB, then press Re-check.",
        ),
        Check(
            "ollama_installed", "Ollama installed", shutil.which("ollama") is not None, "",
            "Install Ollama from https://ollama.com/download, then press Re-check.",
        ),
    ]

    server_ok = model_ok = False
    try:
        OllamaModelClient().ensure_ready()
        server_ok = model_ok = True
    except ModelNotInstalledError:
        server_ok = True
    except (ModelUnavailableError, ValueError):
        pass
    checks.append(Check(
        "ollama_running", "Ollama is running", server_ok, "",
        "Open the Ollama app (or run `ollama serve`), then press Re-check.",
    ))
    checks.append(Check(
        "model", f"AI model {settings.ollama_model}", model_ok, "",
        "Press Download model (about 1.4 GB).",
    ))
    checks.append(Check(
        "gmail_credentials", "Google credentials file", Path(settings.gmail_credentials_file).exists(), "",
        "Upload it in the next step.",
    ))
    checks.append(Check(
        "gmail_connected", "Gmail connected (read-only)", has_stored_token(), "", "Press Connect Gmail.",
    ))
    return checks
