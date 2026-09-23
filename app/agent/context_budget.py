"""Context-budget estimation and chunking for small local models.

Qwen3 1.7B is run with a limited `num_ctx` (see settings.ollama_context_size).
Blowing that budget causes silent truncation by Ollama, which corrupts
structured output. This module estimates prompt size before inference and
splits oversized email bodies into bounded chunks so no single call ever
risks the context window.

Token counts are approximated as `len(text) // CHARS_PER_TOKEN` — no
tokenizer is available for Qwen3 via the Ollama HTTP API, and a rough
heuristic is sufficient for a budget-fitting decision, not billing.
"""
from __future__ import annotations

from dataclasses import dataclass

CHARS_PER_TOKEN = 4

# Fraction of the context window reserved for the model's structured JSON
# output plus a small safety margin. The rest is available for prompt input.
OUTPUT_RESERVATION_RATIO = 0.25


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def input_budget_tokens(context_size: int) -> int:
    """Max input tokens after reserving room for structured output."""
    return int(context_size * (1 - OUTPUT_RESERVATION_RATIO))


@dataclass(frozen=True)
class ChunkPlan:
    chunks: list[str]
    chunked: bool


def chunk_text(text: str, max_tokens: int) -> ChunkPlan:
    """Split text into chunks that each fit within max_tokens, on line
    boundaries so we don't cut a sentence mid-token. Never merges unrelated
    content — this only splits one already-single email's quoted history."""
    max_chars = max(max_tokens * CHARS_PER_TOKEN, CHARS_PER_TOKEN)
    if len(text) <= max_chars:
        return ChunkPlan(chunks=[text] if text else [], chunked=False)

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines(keepends=True):
        if current_len + len(line) > max_chars and current:
            chunks.append("".join(current))
            current = []
            current_len = 0
        if len(line) > max_chars:
            for start in range(0, len(line), max_chars):
                chunks.append(line[start : start + max_chars])
            continue
        current.append(line)
        current_len += len(line)
    if current:
        chunks.append("".join(current))
    return ChunkPlan(chunks=chunks, chunked=True)
