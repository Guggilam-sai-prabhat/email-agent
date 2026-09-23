from app.agent.context_budget import chunk_text, estimate_tokens, input_budget_tokens


def test_input_budget_reserves_room_for_output():
    assert input_budget_tokens(8192) == 6144


def test_chunk_text_returns_single_chunk_when_within_budget():
    plan = chunk_text("short text", max_tokens=100)
    assert plan.chunked is False
    assert plan.chunks == ["short text"]


def test_chunk_text_splits_oversized_text_on_line_boundaries():
    text = "\n".join(f"line {i}" for i in range(1000))
    plan = chunk_text(text, max_tokens=50)
    assert plan.chunked is True
    assert len(plan.chunks) > 1
    assert "".join(plan.chunks) == text


def test_chunk_text_splits_a_single_oversized_line():
    text = "x" * 10000
    plan = chunk_text(text, max_tokens=10)
    assert plan.chunked is True
    assert "".join(plan.chunks) == text
    assert all(len(c) <= 40 for c in plan.chunks)


def test_estimate_tokens_is_roughly_chars_over_four():
    assert estimate_tokens("abcd" * 10) == 10
