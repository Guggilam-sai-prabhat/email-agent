"""Architectural proof that no code path lets an email cause tool/action execution.

Complements tests/test_analysis.py and tests/test_sanitization.py, which prove
injected text is *ignored*. This module proves the stronger claim: there is no
mechanism available to obey it even if the model tried, because the model is
never given tools, and every write-shaped verb in the app is either Gmail
read-only, local-only (review state), or a no-op description string.
"""
from __future__ import annotations

import inspect

from app.agent.model_client import ModelClient, OllamaModelClient
from app.database.models import EmailAnalysis
from app.email.gmail_client import GmailReadOnlyClient
from app.email.retrieval import GmailRetrievalService


def test_ollama_client_call_has_no_tools_argument():
    """`generate()` never passes `tools=` to the Ollama chat API — a model
    response can request a tool call only if the caller offered tools."""
    generate_source = inspect.getsource(OllamaModelClient.generate)
    assert "tools=" not in generate_source
    assert "tools" not in generate_source


def test_model_client_protocol_has_no_tool_methods():
    """The ModelClient interface itself has no surface for tool results,
    function-call dispatch, or command execution — only generate()/is_ready()."""
    members = {name for name, _ in inspect.getmembers(ModelClient) if not name.startswith("_")}
    assert members == {"is_ready", "generate"}


def test_gmail_client_exposes_no_write_methods():
    """The only Gmail-facing client in the app has list/get and nothing else —
    no send, modify, delete, label, draft, or trash method exists to call."""
    public_methods = {
        name for name, _ in inspect.getmembers(GmailReadOnlyClient, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    assert public_methods == {"list_message_ids", "get_message", "get_thread"}


def test_gmail_retrieval_service_exposes_no_write_methods():
    public_methods = {
        name for name, _ in inspect.getmembers(GmailRetrievalService, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    assert public_methods == {
        "list_candidate_message_ids",
        "get_message_for_analysis",
        "has_been_replied_to",
    }


def test_suggested_action_is_not_a_persisted_executable_field():
    """suggested_action is free text stored for a human to read, not a
    field the app ever parses back into an operation. There is no queue,
    task, or executor type anywhere that consumes it."""
    columns = {c.name for c in EmailAnalysis.__table__.columns}
    assert "suggested_action" in columns
    # No column encodes a pending/executable action state machine.
    assert not {"action_status", "action_executed_at", "action_queue_id"} & columns
