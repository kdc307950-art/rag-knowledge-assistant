"""Create a review-gated, corpus-neutral retrieval golden-set template."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


TEMPLATE_CASES = (
    ("factual", "替换为知识库中的稳定事实问题", False),
    ("proper_noun", "替换为知识库中的专有名词问题", False),
    ("multi_document", "替换为需要多个文档共同回答的问题", False),
    ("history_followup", "替换为依赖上一轮语义的追问", False),
    ("expected_refusal", "替换为明确不在知识库中的问题", True),
)


def create_template(output: Path, count: int = 20) -> None:
    if count < len(TEMPLATE_CASES):
        raise ValueError(f"count 至少为 {len(TEMPLATE_CASES)}，以覆盖核心类别")
    lines = [
        "# Template only: replace every placeholder and manually review against the original documents.",
        "# Set review_status to approved only after source/chapter/parent_id/paragraph verification.",
        "# This file intentionally fails evaluation while any case remains unreviewed.",
    ]
    for index in range(1, count + 1):
        tag, query, refusal = TEMPLATE_CASES[(index - 1) % len(TEMPLATE_CASES)]
        case = {
            "schema_version": 2,
            "case_id": f"retrieval_{index:03d}",
            "query": query,
            "expected_sources": [],
            "expected_evidence": [],
            "expected_refusal": refusal,
            "tags": [tag] if tag != "factual" else [],
            "review_status": "unreviewed",
        }
        lines.append(json.dumps(case, ensure_ascii=False))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=20)
    args = parser.parse_args()
    create_template(args.output, args.count)
    print(f"generated template: {args.output}")
    print("review_status=unreviewed; do not use it as a baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
