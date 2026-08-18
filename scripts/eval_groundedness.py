"""Summarize manually reviewed answer-groundedness cases without calling an LLM.

This evaluator checks whether source IDs mentioned in an already generated
answer exist in that answer's retrieval snapshot. Semantic support is supplied
by a human reviewer; no string or heuristic check is treated as entailment.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from enterprise_rag.llm.groundedness import validate_citations  # noqa: E402


SCHEMA_VERSION = 1
CLAIM_VERDICTS = {"supported", "contradicted", "unsupported", "not_reviewed"}


def _load_cases(path: Path) -> tuple[list[dict[str, Any]], str]:
    cases: list[dict[str, Any]] = []
    raw_lines: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        raw_lines.append(line.rstrip())
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number} 不是有效 JSONL: {exc}") from exc
        if not isinstance(case, dict) or case.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"{path}:{line_number} schema_version 必须为 {SCHEMA_VERSION}")
        if not isinstance(case.get("case_id"), str) or not case["case_id"].strip():
            raise ValueError(f"{path}:{line_number} 缺少 case_id")
        if not isinstance(case.get("answer"), str):
            raise ValueError(f"{path}:{line_number} answer 必须是字符串")
        source_ids = case.get("source_ids")
        if not isinstance(source_ids, list) or any(not isinstance(item, str) or not item.strip() for item in source_ids):
            raise ValueError(f"{path}:{line_number} source_ids 必须是非空字符串数组")
        claims = case.get("claims")
        if not isinstance(claims, list) or not claims:
            raise ValueError(f"{path}:{line_number} claims 必须是人工审核的非空数组")
        for claim in claims:
            if (
                not isinstance(claim, dict)
                or not isinstance(claim.get("text"), str)
                or claim.get("verdict") not in CLAIM_VERDICTS
            ):
                raise ValueError(f"{path}:{line_number} claim 需要 text 和合法 verdict")
        cases.append(case)
    if not cases:
        raise ValueError(f"{path} 没有可评估案例；需要人工审核后的回答样本")
    digest = hashlib.sha256(("\n".join(raw_lines) + "\n").encode("utf-8")).hexdigest()
    return cases, digest


def evaluate(cases_path: Path) -> dict[str, Any]:
    cases, digest = _load_cases(cases_path)
    results: list[dict[str, Any]] = []
    verdicts: Counter[str] = Counter()
    valid_citation_cases = 0
    cited_cases = 0
    for case in cases:
        citations = validate_citations(case["answer"], case["source_ids"])
        valid_citation_cases += int(bool(citations["valid"]))
        cited_cases += int(bool(citations["has_citations"]))
        for claim in case["claims"]:
            verdicts[str(claim["verdict"])] += 1
        results.append({
            "case_id": case["case_id"],
            "citation_validation": citations,
            "claim_counts": dict(Counter(str(claim["verdict"]) for claim in case["claims"])),
        })

    reviewed = verdicts["supported"] + verdicts["contradicted"] + verdicts["unsupported"]
    unsupported = verdicts["contradicted"] + verdicts["unsupported"]
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "cases_sha256": digest,
        "metrics": {
            "case_count": len(cases),
            "citation_present_rate": cited_cases / len(cases),
            "citation_id_valid_rate": valid_citation_cases / len(cases),
            "reviewed_claim_count": reviewed,
            "supported_claim_rate": verdicts["supported"] / reviewed if reviewed else None,
            "unsupported_or_contradicted_claim_rate": unsupported / reviewed if reviewed else None,
            "unreviewed_claim_count": verdicts["not_reviewed"],
        },
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(args.cases)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
