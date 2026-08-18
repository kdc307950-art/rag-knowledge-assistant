"""Regression coverage for the bounded observability metric helpers."""

from __future__ import annotations


def test_llm_token_and_retrieval_metrics_render_with_semantic_labels():
    from backend.observability.metrics import (
        mark_kb_busy,
        mark_llm_tokens,
        mark_refusal,
        mark_retrieval,
        render_metrics,
        reset_metrics,
    )

    reset_metrics()
    mark_llm_tokens("input", 11, "request")
    mark_llm_tokens("output", 7, "stream")
    mark_llm_tokens("unknown", 99, "stream")
    mark_llm_tokens("input", -1, "stream")
    mark_retrieval("hit", 3, 0.9)
    mark_retrieval("empty", 0)
    mark_retrieval("error")
    mark_kb_busy("retrieval")
    mark_refusal()

    rendered = render_metrics()

    assert 'rag_llm_tokens_total{type="input",mode="request"} 11' in rendered
    assert 'rag_llm_tokens_total{type="output",mode="stream"} 7' in rendered
    assert 'rag_retrievals_total{outcome="hit"} 1' in rendered
    assert 'rag_retrievals_total{outcome="empty"} 1' in rendered
    assert 'rag_retrievals_total{outcome="error"} 1' in rendered
    assert 'rag_refusals_total 1' in rendered
    assert 'rag_kb_busy_total{reason="retrieval"} 1' in rendered
    assert "rag_retrieval_results_histogram_count 2" in rendered
    assert "rag_retrieval_top_score_histogram_count 1" in rendered


def test_metric_helpers_ignore_malformed_values():
    from backend.observability.metrics import (
        mark_llm_tokens,
        mark_retrieval,
        render_metrics,
        reset_metrics,
    )

    reset_metrics()
    mark_llm_tokens("input", "not-a-number", "request")
    mark_retrieval("unknown", "bad", float("nan"))

    rendered = render_metrics()

    assert "rag_llm_tokens_total{" not in rendered
    assert 'rag_retrievals_total{outcome="error"} 1' in rendered
    assert "rag_retrieval_results_histogram_count" not in rendered
