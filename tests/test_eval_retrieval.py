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

    assert calls == [
        ("命中", {"return_raw": True, "retrieval_policy": "authoritative"}),
        ("无答案", {"return_raw": True, "retrieval_policy": "all_active"}),
    ]
    assert report["metrics"]["recall_at_1"] == 1.0
    assert report["metrics"]["recall_at_3"] == 1.0
    assert report["metrics"]["mrr"] == 1.0
    assert report["metrics"]["refusal_accuracy"] == 1.0
    assert report["metrics"]["answerable_false_refusal_rate"] == 0.0
    assert report["metrics"]["evidence_recall_at_1"] is None
    assert report["config"]["document_governance_policy"] in {
        "unresolved", "authoritative", "all_active"
    }
    assert report["config"]["document_governance_sha256"]


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


def test_evaluator_accepts_corpus_defined_tags(monkeypatch, tmp_path):
    from scripts import eval_retrieval

    cases_path = tmp_path / "custom-tags.jsonl"
    cases_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "case_id": "custom",
                "query": "命中",
                "tags": ["HR", "out_of_scope", "finance-2025"],
                "expected_sources": ["a.txt"],
                "expected_refusal": False,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        eval_retrieval,
        "retrieve_context",
        lambda *_args, **_kwargs: ("context", ["a.txt"], [{"source": "a.txt"}]),
    )

    report = eval_retrieval.evaluate(cases_path)
    assert report["metrics"]["case_count"] == 1


def test_evaluator_rejects_unsafe_tag_format(tmp_path):
    from scripts import eval_retrieval

    cases_path = tmp_path / "unsafe-tags.jsonl"
    cases_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "case_id": "unsafe",
                "query": "命中",
                "tags": ["HR/finance"],
                "expected_sources": ["a.txt"],
                "expected_refusal": False,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    try:
        eval_retrieval.evaluate(cases_path)
    except ValueError as exc:
        assert "ASCII" in str(exc)
    else:
        raise AssertionError("unsafe tag format should be rejected")


def test_source_policies_produce_separate_metrics(monkeypatch, tmp_path):
    from scripts import eval_retrieval

    cases_path = tmp_path / "source-policies.jsonl"
    cases = [
        {
            "schema_version": 2,
            "case_id": "authority",
            "query": "authority",
            "source_policy": "authoritative_only",
            "expected_sources": ["a.txt"],
            "expected_evidence": [{"source": "a.txt", "parent_id": "p-a"}],
            "expected_refusal": False,
        },
        {
            "schema_version": 2,
            "case_id": "all",
            "query": "all",
            "source_policy": "all_required",
            "expected_sources": ["a.txt", "b.txt"],
            "expected_evidence": [{"source": "a.txt", "parent_id": "p-a"}],
            "expected_refusal": False,
        },
        {
            "schema_version": 2,
            "case_id": "equivalent",
            "query": "equivalent",
            "source_policy": "any_equivalent",
            "source_groups": [["a.txt", "b.txt"], ["c.txt"]],
            "expected_sources": ["a.txt", "b.txt", "c.txt"],
            "expected_evidence": [{"source": "a.txt", "parent_id": "p-a"}],
            "expected_refusal": False,
        },
    ]
    cases_path.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n",
        encoding="utf-8",
    )

    def fake_retrieve(_query, **_kwargs):
        return "context", [], [
            {"source": "a.txt", "parent_id": "p-a", "rerank_score": 0.9},
            {"source": "c.txt", "parent_id": "p-c", "rerank_score": 0.8},
        ]

    monkeypatch.setattr(eval_retrieval, "retrieve_context", fake_retrieve)
    report = eval_retrieval.evaluate(cases_path)

    assert report["metrics"]["authoritative_recall_at_1"] == 1.0
    assert report["metrics"]["multi_document_coverage_at_1"] == 0.5
    assert report["metrics"]["any_equivalent_hit_rate_at_1"] == 0.5
    assert report["metrics"]["source_policy_counts"] == {
        "authoritative_only": 1,
        "all_required": 1,
        "any_equivalent": 1,
    }
