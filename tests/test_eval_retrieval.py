"""Contract tests for the explicit retrieval evaluation command."""

from __future__ import annotations

import json


def test_evaluator_calls_retrieval_directly_and_calculates_metrics(monkeypatch, tmp_path):
    from scripts import eval_retrieval

    cases_path = tmp_path / "retrieval_cases.jsonl"
    cases_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "schema_version": 1,
                        "case_id": "hit",
                        "query": "命中",
                        "tags": ["proper_noun"],
                        "expected_sources": ["a.txt"],
                        "expected_refusal": False,
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "schema_version": 1,
                        "case_id": "reject",
                        "query": "无答案",
                        "tags": ["no_answer", "expected_refusal"],
                        "expected_sources": [],
                        "expected_refusal": True,
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    calls = []

    def fake_retrieve(query, **kwargs):
        calls.append((query, kwargs))
        if query == "命中":
            return "context", ["a.txt"], [
                {"source": "a.txt", "rerank_score": 0.9},
                {"source": "b.txt", "rerank_score": 0.2},
            ]
        return "", [], []

    monkeypatch.setattr(eval_retrieval, "retrieve_context", fake_retrieve)
    report = eval_retrieval.evaluate(cases_path)

    assert all(kwargs == {"return_raw": True} for _query, kwargs in calls)
    assert report["metrics"]["recall_at_1"] == 1.0
    assert report["metrics"]["recall_at_3"] == 1.0
    assert report["metrics"]["mrr"] == 1.0
    assert report["metrics"]["refusal_accuracy"] == 1.0
    assert report["metrics"]["answerable_false_refusal_rate"] == 0.0
    assert report["metrics"]["evidence_recall_at_1"] is None


def test_evaluator_rejects_ambiguous_case_schema(tmp_path):
    from scripts import eval_retrieval

    cases_path = tmp_path / "bad.jsonl"
    cases_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "case_id": "ambiguous",
                "query": "问题",
                "tags": [],
                "expected_sources": [],
                "expected_refusal": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    try:
        eval_retrieval.evaluate(cases_path)
    except ValueError as exc:
        assert "expected_sources" in str(exc)
    else:
        raise AssertionError("ambiguous case should be rejected")


def test_v2_requires_evidence_and_scores_parent_anchor(monkeypatch, tmp_path):
    from scripts import eval_retrieval

    cases_path = tmp_path / "v2.jsonl"
    cases_path.write_text(
        json.dumps({
            "schema_version": 2,
            "case_id": "evidence",
            "query": "命中",
            "tags": ["proper_noun"],
            "expected_sources": ["a.txt"],
            "expected_evidence": [{"source": "a.txt", "parent_id": "p1"}],
            "expected_refusal": False,
        }, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        eval_retrieval, "retrieve_context",
        lambda *_args, **_kwargs: ("context", ["a.txt"], [{"source": "a.txt", "parent_id": "p1", "rerank_score": 0.9}]),
    )

    report = eval_retrieval.evaluate(cases_path)

    assert report["metrics"]["evidence_recall_at_1"] == 1.0
    assert report["metrics"]["evidence_mrr"] == 1.0


def test_evidence_paragraph_anchor_matches_comma_separated_metadata():
    from scripts.eval_retrieval import _evidence_matches

    assert _evidence_matches(
        {"source": "book.txt", "chapter": "第一章", "paragraph": 3},
        {"source": "book.txt", "chapter": "第一章", "paragraph": "1,2,3,4"},
    )
    assert not _evidence_matches(
        {"source": "book.txt", "chapter": "第一章", "paragraph": 9},
        {"source": "book.txt", "chapter": "第一章", "paragraph": "1,2,3,4"},
    )
