"""Local model adapter — Ollama/Qwen3, no tools, no network egress from the model.

The model is never given tool access, filesystem access, or command execution.
It is a pure text-in/text-out call. Callers must not log prompts or raw
completions (see app.logging_config's forbidden log keys).
"""
from __future__ import annotations

from typing import Any, Protocol

import httpx
from ollama import Client, ResponseError

from app.config import settings

# Only these hosts may be contacted by default, even if OLLAMA_BASE_URL is
# misconfigured — the model adapter must never reach an arbitrary network host.
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ModelUnavailableError(RuntimeError):
    """Raised when Ollama cannot be reached at all (connection/timeout)."""


class ModelNotInstalledError(RuntimeError):
    """Raised when Ollama is reachable but the configured model isn't pulled."""


class ModelClient(Protocol):
    def is_ready(self) -> bool:
        """Return True if the model is installed and Ollama is reachable."""
        ...

    def generate(self, prompt: str, format: dict[str, Any] | None = None) -> str:
        """Run a single-turn, non-streaming completion. Returns raw text.

        `format` is an optional JSON schema (e.g. from Pydantic's
        `model_json_schema()`) constraining the model's structured output.
        """
        ...


def _validate_host(base_url: str) -> None:
    host = base_url.split("://", 1)[-1].split(":", 1)[0].split("/", 1)[0]
    if host not in _ALLOWED_HOSTS:
        raise ValueError(
            f"OLLAMA_BASE_URL host {host!r} is not an allowed local address; "
            f"allowed hosts are {sorted(_ALLOWED_HOSTS)}"
        )


class OllamaModelClient:
    """ModelClient backed by a local Ollama server."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        connect_timeout: float = 5.0,
        inference_timeout: float = 120.0,
    ) -> None:
        self._base_url = base_url or settings.ollama_base_url
        _validate_host(self._base_url)
        self._model = model or settings.ollama_model
        self._client = Client(
            host=self._base_url,
            timeout=httpx.Timeout(inference_timeout, connect=connect_timeout),
        )

    def is_ready(self) -> bool:
        try:
            self.ensure_ready()
            return True
        except (ModelUnavailableError, ModelNotInstalledError):
            return False

    def ensure_ready(self) -> None:
        """Startup check. Raises a clear error instead of pulling a model."""
        try:
            self._client.show(self._model)
        except ResponseError as exc:
            raise ModelNotInstalledError(
                f"Model {self._model!r} is not installed in Ollama at {self._base_url}. "
                f"Run: ollama pull {self._model}"
            ) from exc
        except (httpx.ConnectError, httpx.TimeoutException, ConnectionError) as exc:
            raise ModelUnavailableError(
                f"Could not reach Ollama at {self._base_url}. Is `ollama serve` running?"
            ) from exc

    def generate(self, prompt: str, format: dict[str, Any] | None = None) -> str:
        try:
            response = self._client.chat(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                think=False,
                stream=False,
                format=format,
                options={
                    "temperature": 0,
                    "seed": 0,
                    "top_p": 1.0,
                    "num_ctx": settings.ollama_context_size,
                },
            )
        except ResponseError as exc:
            if exc.status_code == 404:
                raise ModelNotInstalledError(
                    f"Model {self._model!r} is not installed in Ollama at {self._base_url}. "
                    f"Run: ollama pull {self._model}"
                ) from exc
            raise
        except (httpx.ConnectError, httpx.TimeoutException, ConnectionError) as exc:
            raise ModelUnavailableError(
                f"Could not reach Ollama at {self._base_url}. Is `ollama serve` running?"
            ) from exc

        content = getattr(getattr(response, "message", None), "content", None)
        if not content:
            raise ValueError("Model returned an empty or malformed response")
        return content
