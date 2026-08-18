"""Run the versioned, LLM-free retrieval golden-set evaluation.

This is an explicit regression command, not part of the normal pytest suite.
It calls ``retrieve_context`` directly so history rewriting and generation are
not involved. The active runtime data directory and model assets are selected
through the same environment variables as the application.
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

from enterprise_rag.config import (  # noqa: E402
    EMBEDDING_BATCH_SIZE,
    MODEL_NAME,
    MODEL_VERSION,
    RERANKER_MODEL,
    RERANK_SCORE_THRESHOLD,
    RETRIEVAL_VERSION,
    HYBRID_ALPHA,
    FINAL_TOP_K,
)
from enterprise_rag.core.exceptions import KnowledgeBaseBusyError  # noqa: E402
from enterprise_rag.rag.retriever import retrieve_context  # noqa: E402


SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = {1, SCHEMA_VERSION}
K_VALUES = (1, 3, 5)
SUPPORTED_TAGS = {
    "no_answer",
    "multi_document",
    "proper_noun",
    "history_followup",
    "expected_refusal",
}


def _load_cases(path: Path) -> tuple[list[dict[str, Any]], str]:
    records: list[dict[str, Any]] = []
    raw_lines: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        raw_lines.append(line.rstrip())
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number} 不是有效 JSONL: {exc}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{line_number} 必须是 JSON 对象")
        schema_version = record.get("schema_version")
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f"{path}:{line_number} schema_version 必须为 1 或 {SCHEMA_VERSION}")
        if not str(record.get("case_id") or "").strip():
            raise ValueError(f"{path}:{line_number} 缺少 case_id")
        if not str(record.get("query") or "").strip():
            raise ValueError(f"{path}:{line_number} 缺少 query")
        tags = record.get("tags") or []
        if not isinstance(tags, list) or any(tag not in SUPPORTED_TAGS for tag in tags):
            raise ValueError(f"{path}:{line_number} tags 含未知值")
        expected_sources = record.get("expected_sources") or []
        if not isinstance(expected_sources, list) or any(not str(item).strip() for item in expected_sources):
            raise ValueError(f"{path}:{line_number} expected_sources 必须是字符串数组")
        expected_refusal = bool(record.get("expected_refusal", False))
        expected_evidence = record.get("expected_evidence") or []
        if not isinstance(expected_evidence, list):
            raise ValueError(f"{path}:{line_number} expected_evidence 必须是数组")
        for evidence in expected_evidence:
            if not isinstance(evidence, dict) or not str(evidence.get("source") or "").strip():
                raise ValueError(f"{path}:{line_number} expected_evidence 每项必须包含 source")
            anchors = [evidence.get(name) for name in ("parent_id", "chapter", "paragraph")]
            if not any(value is not None and str(value).strip() for value in anchors):
                raise ValueError(
                    f"{path}:{line_number} expected_evidence 需要 parent_id/chapter/paragraph 锚点"
                )
        if schema_version == SCHEMA_VERSION and not expected_refusal and not expected_evidence:
            raise ValueError(f"{path}:{line_number} v2 可回答案例必须人工标注 expected_evidence")
        if expected_refusal and expected_sources:
            raise ValueError(f"{path}:{line_number} 拒答案例不能同时指定 expected_sources")
        if not expected_refusal and not expected_sources and "no_answer" not in tags:
            raise ValueError(
                f"{path}:{line_number} 非拒答案例必须指定 expected_sources，或标记 no_answer"
            )
        if expected_refusal and "expected_refusal" not in tags:
            raise ValueError(f"{path}:{line_number} 拒答案例必须包含 expected_refusal 标签")
        record["tags"] = tags
        record["expected_sources"] = [str(item) for item in expected_sources]
        record["expected_refusal"] = expected_refusal
        record["expected_evidence"] = expected_evidence
        record["schema_version"] = schema_version
        records.append(record)
    if not records:
        raise ValueError(f"{path} 没有可评估案例；golden set 需要人工标注后再运行")
    digest = hashlib.sha256(("\n".join(raw_lines) + "\n").encode("utf-8")).hexdigest()
    return records, digest


def _sources_at(raw_results: list[dict[str, Any]], k: int) -> set[str]:
    return {
        str(item.get("source") or "")
        for item in raw_results[:k]
        if str(item.get("source") or "").strip()
    }


def _evidence_matches(expected: dict[str, Any], result: dict[str, Any]) -> bool:
    if str(expected.get("source") or "") != str(result.get("source") or ""):
        return False
    for field in ("parent_id", "chapter", "paragraph"):
        value = expected.get(field)
        if value is None or not str(value).strip():
            continue
        actual = str(result.get(field) or "").strip()
        if field == "paragraph":
            expected_tokens = {token.strip() for token in str(value).split(",") if token.strip()}
            actual_tokens = {token.strip() for token in actual.split(",") if token.strip()}
            if not expected_tokens.intersection(actual_tokens):
                return False
        elif str(value).strip() != actual:
            return False
    return True


def _evidence_at(raw_results: list[dict[str, Any]], k: int, expected: list[dict[str, Any]]) -> float | None:
    if not expected:
        return None
    matched = sum(
        any(_evidence_matches(item, result) for result in raw_results[:k])
        for item in expected
    )
    return matched / len(expected)


def _evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    # Deliberately call retrieve_context instead of RagService.retrieve_only:
    # the latter may invoke LLM query rewriting for history-dependent prompts.
    try:
        _context, _sources, raw_results = retrieve_context(
            str(case["query"]),
            return_raw=True,
        )
    except KnowledgeBaseBusyError:
        return {
            "case_id": case["case_id"],
            "status": "busy",
            "expected_refusal": bool(case["expected_refusal"]),
            "expected_sources": case["expected_sources"],
            "ranked_sources": [],
        }
    except Exception as exc:
        return {
            "case_id": case["case_id"],
            "status": "error",
            "error_type": type(exc).__name__,
            "expected_refusal": bool(case["expected_refusal"]),
            "expected_sources": case["expected_sources"],
            "ranked_sources": [],
        }

    ranked_sources = [str(item.get("source") or "") for item in raw_results]
    expected = set(case["expected_sources"])
    expected_evidence = case.get("expected_evidence", [])
    is_refusal = not bool(raw_results)
    recalls: dict[str, float | None] = {}
    for k in K_VALUES:
        if case["expected_refusal"]:
            recalls[str(k)] = None
        elif expected:
            recalls[str(k)] = len(expected & _sources_at(raw_results, k)) / len(expected)
        else:
            recalls[str(k)] = None

    reciprocal_rank = 0.0
    if expected:
        for index, source in enumerate(ranked_sources, 1):
            if source in expected:
                reciprocal_rank = 1.0 / index
                break
    evidence_reciprocal_rank = 0.0
    if expected_evidence:
        for index, result in enumerate(raw_results, 1):
            if any(_evidence_matches(item, result) for item in expected_evidence):
                evidence_reciprocal_rank = 1.0 / index
                break

    return {
        "case_id": case["case_id"],
        "status": "ok",
        "expected_refusal": bool(case["expected_refusal"]),
        "actual_refusal": is_refusal,
        "expected_sources": case["expected_sources"],
        "expected_evidence": expected_evidence,
        "ranked_sources": ranked_sources[:FINAL_TOP_K],
        "ranked_evidence": [
            {
                "source": item.get("source"),
                "parent_id": item.get("parent_id"),
                "chapter": item.get("chapter"),
                "paragraph": item.get("paragraph"),
            }
            for item in raw_results[:FINAL_TOP_K]
        ],
        "top_score": max(
            (float(item.get("rerank_score", 0.0)) for item in raw_results),
            default=0.0,
        ),
        "recall": recalls,
        "reciprocal_rank": reciprocal_rank,
        "evidence_recall": {
            str(k): _evidence_at(raw_results, k, expected_evidence) for k in K_VALUES
        },
        "evidence_reciprocal_rank": evidence_reciprocal_rank,
    }


def evaluate(cases_path: Path) -> dict[str, Any]:
    cases, cases_sha256 = _load_cases(cases_path)
    results = [_evaluate_case(case) for case in cases]
    answerable = [item for item in results if not item["expected_refusal"] and item["status"] == "ok"]
    refusal_cases = [item for item in results if item["expected_refusal"] and item["status"] == "ok"]
    metrics: dict[str, Any] = {}
    for k in K_VALUES:
        values = [item["recall"][str(k)] for item in answerable if item["recall"][str(k)] is not None]
        metrics[f"recall_at_{k}"] = sum(values) / len(values) if values else None
    reciprocal_ranks = [item["reciprocal_rank"] for item in answerable]
    metrics["mrr"] = sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else None
    evidence_cases = [item for item in answerable if item.get("expected_evidence")]
    for k in K_VALUES:
        values = [
            item["evidence_recall"][str(k)]
            for item in evidence_cases
            if item["evidence_recall"][str(k)] is not None
        ]
        metrics[f"evidence_recall_at_{k}"] = sum(values) / len(values) if values else None
    evidence_ranks = [item["evidence_reciprocal_rank"] for item in evidence_cases]
    metrics["evidence_mrr"] = (
        sum(evidence_ranks) / len(evidence_ranks) if evidence_ranks else None
    )
    metrics["evidence_evaluable_count"] = len(evidence_cases)
    metrics["refusal_accuracy"] = (
        sum(item.get("actual_refusal") is True for item in refusal_cases) / len(refusal_cases)
        if refusal_cases
        else None
    )
    metrics["answerable_false_refusal_rate"] = (
        sum(item.get("actual_refusal") is True for item in answerable) / len(answerable)
        if answerable
        else None
    )
    metrics["case_count"] = len(results)
    metrics["status_counts"] = dict(Counter(item["status"] for item in results))
    metrics["evaluation_error_rate"] = sum(
        item["status"] == "error" for item in results
    ) / len(results)
    metrics["busy_rate"] = sum(
        item["status"] == "busy" for item in results
    ) / len(results)
    metrics["answerable_evaluable_count"] = len(answerable)
    metrics["refusal_evaluable_count"] = len(refusal_cases)
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "cases_sha256": cases_sha256,
        "config": {
            "retrieval_version": RETRIEVAL_VERSION,
            "model_version": MODEL_VERSION,
            "embedding_model": MODEL_NAME,
            "reranker_model": RERANKER_MODEL,
            "rerank_score_threshold": RERANK_SCORE_THRESHOLD,
            "hybrid_alpha": HYBRID_ALPHA,
            "final_top_k": FINAL_TOP_K,
            "embedding_batch_size": EMBEDDING_BATCH_SIZE,
        },
        "metrics": metrics,
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
