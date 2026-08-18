"""Contract tests for the manual groundedness evaluator."""

from __future__ import annotations

import json


def test_groundedness_evaluator_separates_citation_integrity_from_human_support(tmp_path):
    from scripts import eval_groundedness

    path = tmp_path / "groundedness.jsonl"
    path.write_text(
        json.dumps({
            "schema_version": 1,
            "case_id": "answer-1",
            "answer": "制度为五天。[S1] 另一个结论[S9]",
            "source_ids": ["S1", "S2"],
            "claims": [
                {"text": "制度为五天", "verdict": "supported"},
                {"text": "另一个结论", "verdict": "unsupported"},
            ],
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    report = eval_groundedness.evaluate(path)

    assert report["metrics"]["citation_present_rate"] == 1.0
    assert report["metrics"]["citation_id_valid_rate"] == 0.0
    assert report["metrics"]["supported_claim_rate"] == 0.5
    assert report["metrics"]["unsupported_or_contradicted_claim_rate"] == 0.5


def test_groundedness_evaluator_requires_human_claim_review(tmp_path):
    from scripts import eval_groundedness

    path = tmp_path / "invalid.jsonl"
    path.write_text(
        json.dumps({
            "schema_version": 1, "case_id": "bad", "answer": "正文",
            "source_ids": ["S1"], "claims": [],
        }) + "\n",
        encoding="utf-8",
    )

    try:
        eval_groundedness.evaluate(path)
    except ValueError as exc:
        assert "claims" in str(exc)
    else:
        raise AssertionError("manual claims are mandatory")
