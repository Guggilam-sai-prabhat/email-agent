from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from ollama import ResponseError

from app.agent.model_client import (
    ModelNotInstalledError,
    ModelUnavailableError,
    OllamaModelClient,
)


def _client_with_mocked_ollama():
    with patch("app.agent.model_client.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        client = OllamaModelClient(base_url="http://127.0.0.1:11434", model="qwen3:1.7b")
    return client, mock_client


def test_generate_returns_content_on_success():
    client, mock_client = _client_with_mocked_ollama()
    mock_client.chat.return_value = SimpleNamespace(message=SimpleNamespace(content="hello world"))

    result = client.generate("summarize this")

    assert result == "hello world"
    _, kwargs = mock_client.chat.call_args
    assert kwargs["think"] is False
    assert kwargs["options"]["temperature"] == 0


def test_generate_raises_on_timeout():
    client, mock_client = _client_with_mocked_ollama()
    mock_client.chat.side_effect = httpx.TimeoutException("timed out")

    with pytest.raises(ModelUnavailableError):
        client.generate("summarize this")


def test_generate_raises_on_connection_error():
    client, mock_client = _client_with_mocked_ollama()
    mock_client.chat.side_effect = httpx.ConnectError("refused")

    with pytest.raises(ModelUnavailableError):
        client.generate("summarize this")


def test_generate_raises_on_malformed_response():
    client, mock_client = _client_with_mocked_ollama()
    mock_client.chat.return_value = SimpleNamespace(message=SimpleNamespace(content=""))

    with pytest.raises(ValueError):
        client.generate("summarize this")


def test_generate_raises_model_not_installed_on_404():
    client, mock_client = _client_with_mocked_ollama()
    mock_client.chat.side_effect = ResponseError("model not found", status_code=404)

    with pytest.raises(ModelNotInstalledError):
        client.generate("summarize this")


def test_ensure_ready_raises_when_model_missing():
    client, mock_client = _client_with_mocked_ollama()
    mock_client.show.side_effect = ResponseError("model not found", status_code=404)

    with pytest.raises(ModelNotInstalledError):
        client.ensure_ready()


def test_ensure_ready_raises_when_ollama_unreachable():
    client, mock_client = _client_with_mocked_ollama()
    mock_client.show.side_effect = httpx.ConnectError("refused")

    with pytest.raises(ModelUnavailableError):
        client.ensure_ready()


def test_is_ready_true_when_model_present():
    client, mock_client = _client_with_mocked_ollama()
    mock_client.show.return_value = SimpleNamespace(model="qwen3:1.7b")

    assert client.is_ready() is True


def test_is_ready_false_when_unavailable():
    client, mock_client = _client_with_mocked_ollama()
    mock_client.show.side_effect = httpx.ConnectError("refused")

    assert client.is_ready() is False


def test_rejects_non_local_base_url():
    with patch("app.agent.model_client.Client"):
        with pytest.raises(ValueError):
            OllamaModelClient(base_url="http://example.com:11434", model="qwen3:1.7b")


def test_allows_localhost_hostname():
    with patch("app.agent.model_client.Client"):
        OllamaModelClient(base_url="http://localhost:11434", model="qwen3:1.7b")
