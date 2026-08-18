"""Golden-set templates must not be accepted as evaluation baselines."""

from __future__ import annotations


def test_template_covers_categories_and_is_review_gated(tmp_path):
    from scripts import create_golden_set_template, eval_retrieval

    path = tmp_path / "golden.jsonl"
    create_golden_set_template.create_template(path, count=5)
    text = path.read_text(encoding="utf-8")
    assert "review_status" in text
    assert '"proper_noun"' in text
    assert '"expected_refusal"' in text

    try:
        eval_retrieval.evaluate(path)
    except ValueError as exc:
        assert "review_status" in str(exc)
    else:
        raise AssertionError("unreviewed template must not run")
