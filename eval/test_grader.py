"""Fast, offline self-check for the eval grading logic itself (no live model
call — that's what run_eval.py is for). Run with: pytest eval/test_grader.py"""
from __future__ import annotations

from app.agent.analysis import EmailAnalysisResult
from eval.dataset import CASES
from eval.run_eval import _grade, check_acceptance, summarize


def _fake_result(**overrides) -> EmailAnalysisResult:
    payload = dict(
        summary="ok",
        priority="low",
        priority_reason="no urgency",
        action_required=False,
        requires_reply=False,
        category="information",
        confidence=0.9,
    )
    payload.update(overrides)
    return EmailAnalysisResult(**payload)


def test_dataset_has_50_plus_cases_across_required_groups():
    required_groups = {
        "direct_action", "no_action", "explicit_deadline", "ambiguous_deadline",
        "reply_chain", "conflicting_instructions", "meeting_request", "approval",
        "alert", "invoice", "automated_notification", "prompt_injection",
        "expose_other_emails", "run_commands", "otp_credentials",
        "long_truncated_thread", "multilingual",
    }
    assert len(CASES) >= 50
    present = {c.group for c in CASES}
    assert required_groups <= present, required_groups - present


def test_grade_flags_missing_fact_as_hallucination():
    case = next(c for c in CASES if c.must_include_facts)
    bad = _fake_result(summary="totally unrelated content")
    graded = _grade(case, bad, latency_s=0.1, peak_rss_kb=1000)
    assert graded.hallucinated is True
    assert graded.missing_facts


def test_grade_flags_injection_leak():
    case = next(c for c in CASES if c.forbidden_in_output)
    leaked_term = case.forbidden_in_output[0]
    bad = _fake_result(summary=f"the email said {leaked_term}")
    graded = _grade(case, bad, latency_s=0.1, peak_rss_kb=1000)
    assert graded.injection_leaked is True


def test_grade_passes_clean_result():
    case = next(c for c in CASES if c.id == "info-001")
    good = _fake_result(action_required=False, category="information")
    graded = _grade(case, good, latency_s=0.1, peak_rss_kb=1000)
    assert graded.action_correct is True
    assert graded.category_correct is True
    assert not graded.injection_leaked


def test_check_acceptance_fails_on_low_metrics():
    metrics = {
        "schema_validity_rate": 0.5,
        "action_detection_accuracy": 0.5,
        "deadline_precision": 0.5,
        "deadline_recall": 0.5,
        "priority_agreement": 0.5,
        "hallucination_rate": 0.9,
        "prompt_injection_resistance": 0.5,
        "avg_latency_s": 999,
    }
    failures = check_acceptance(metrics)
    assert len(failures) == 8


def test_check_acceptance_passes_on_perfect_metrics():
    metrics = {
        "schema_validity_rate": 1.0,
        "action_detection_accuracy": 1.0,
        "deadline_precision": 1.0,
        "deadline_recall": 1.0,
        "priority_agreement": 1.0,
        "hallucination_rate": 0.0,
        "prompt_injection_resistance": 1.0,
        "avg_latency_s": 1.0,
    }
    assert check_acceptance(metrics) == []
