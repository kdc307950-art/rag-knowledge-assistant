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
import re
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
    DOCUMENT_GOVERNANCE_PATH,
)
from enterprise_rag.core.exceptions import KnowledgeBaseBusyError  # noqa: E402
from enterprise_rag.rag.retriever import retrieve_context  # noqa: E402
from enterprise_rag.storage.document_governance import retrieval_policy  # noqa: E402
from enterprise_rag.storage.kb_manifest import get_manifest  # noqa: E402


SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = {1, SCHEMA_VERSION}
K_VALUES = (1, 3, 5)
TAG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SOURCE_POLICIES = {"authoritative_only", "all_required", "any_equivalent"}
EVALUATION_PROFILE = "retrieval-v2"


def _validate_tags(path: Path, line_number: int, value: Any) -> list[str]:
    """Validate corpus-defined tags without coupling scoring to one domain."""
    if value is None:
        return []
    if not isinstance(value, list) or any(
        not isinstance(tag, str) or not TAG_PATTERN.fullmatch(tag) for tag in value
    ):
        raise ValueError(
            f"{path}:{line_number} tags 必须是 ASCII 标识符数组（字母/数字/下划线/连字符）"
        )
    return list(dict.fromkeys(value))


def _validate_source_groups(path: Path, line_number: int, value: Any) -> list[list[str]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{path}:{line_number} any_equivalent 必须包含非空 source_groups")
    groups: list[list[str]] = []
    for group in value:
        if not isinstance(group, list) or not group or any(
            not isinstance(source, str) or not source.strip() for source in group
        ):
            raise ValueError(f"{path}:{line_number} source_groups 必须是非空字符串数组的数组")
        groups.append(list(dict.fromkeys(str(source) for source in group)))
    return groups


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
        review_status = record.get("review_status")
        if review_status is not None and review_status != "approved":
            raise ValueError(
                f"{path}:{line_number} review_status 必须为 approved；模板或未审核案例不能跑基线"
            )
        tags = _validate_tags(path, line_number, record.get("tags"))
        expected_sources = record.get("expected_sources") or []
        if not isinstance(expected_sources, list) or any(not str(item).strip() for item in expected_sources):
            raise ValueError(f"{path}:{line_number} expected_sources 必须是字符串数组")
        source_policy = record.get("source_policy")
        if source_policy is None:
            # Backward compatibility for v1/v2 files written before source
            # policy was introduced: one source is authoritative-only, while
            # multiple sources preserve the historical all-required behavior.
            source_policy = "all_required" if len(expected_sources) > 1 else "authoritative_only"
        if source_policy not in SOURCE_POLICIES:
            raise ValueError(f"{path}:{line_number} source_policy 含未知值")
        source_groups = record.get("source_groups") or []
        if source_policy == "any_equivalent":
            source_groups = _validate_source_groups(path, line_number, source_groups)
            grouped_sources = {
                source for group in source_groups for source in group
            }
            if expected_sources and not set(map(str, expected_sources)).issubset(grouped_sources):
                raise ValueError(
                    f"{path}:{line_number} expected_sources 必须属于 source_groups"
                )
            if not expected_sources:
                expected_sources = sorted(grouped_sources)
        elif source_groups:
            raise ValueError(
                f"{path}:{line_number} source_groups 只能用于 any_equivalent"
            )
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
        if (
            not expected_refusal
            and not expected_sources
            and source_policy != "any_equivalent"
            and "no_answer" not in tags
        ):
            raise ValueError(
                f"{path}:{line_number} 非拒答案例必须指定 expected_sources，或标记 no_answer"
            )
        if not expected_refusal and source_policy == "authoritative_only" and len(expected_sources) != 1:
            raise ValueError(
                f"{path}:{line_number} authoritative_only 必须指定一个 expected_sources"
            )
        if expected_refusal and "expected_refusal" not in tags:
            raise ValueError(f"{path}:{line_number} 拒答案例必须包含 expected_refusal 标签")
        record["tags"] = tags
        record["expected_sources"] = [str(item) for item in expected_sources]
        record["expected_refusal"] = expected_refusal
        record["expected_evidence"] = expected_evidence
        record["schema_version"] = schema_version
        record["source_policy"] = source_policy
        record["source_groups"] = source_groups
        if review_status is not None:
            record["review_status"] = review_status
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


def _evidence_at(
    raw_results: list[dict[str, Any]],
    k: int,
    expected: list[dict[str, Any]],
    *,
    policy: str = "all_required",
    source_groups: list[list[str]] | None = None,
) -> float | None:
    if not expected:
        return None
    if policy == "any_equivalent":
        groups = source_groups or []
        if not groups:
            return None
        matched_groups = 0
        for group in groups:
            group_expected = [
                item for item in expected if str(item.get("source") or "") in group
            ]
            if any(
                any(_evidence_matches(item, result) for item in group_expected)
                for result in raw_results[:k]
            ):
                matched_groups += 1
        return matched_groups / len(groups)
    matched = sum(
        any(_evidence_matches(item, result) for result in raw_results[:k])
        for item in expected
    )
    return matched / len(expected)


def _source_policy_score(
    raw_results: list[dict[str, Any]],
    k: int,
    *,
    policy: str,
    expected_sources: list[str],
    source_groups: list[list[str]],
) -> float | None:
    ranked = _sources_at(raw_results, k)
    if policy == "any_equivalent":
        if not source_groups:
            return None
        return sum(bool(ranked.intersection(group)) for group in source_groups) / len(source_groups)
    if not expected_sources:
        return None
    if policy == "authoritative_only":
        return float(expected_sources[0] in ranked)
    return len(set(expected_sources) & ranked) / len(expected_sources)


def _source_policy_sources(
    *, policy: str, expected_sources: list[str], source_groups: list[list[str]]
) -> set[str]:
    if policy == "any_equivalent":
        return {source for group in source_groups for source in group}
    return set(expected_sources)


def _retrieval_policy_for_case(case: dict[str, Any]) -> str:
    """Keep evaluation explicit when production ordinary retrieval is fail-closed."""
    if bool(case.get("expected_refusal")):
        return "all_active"
    if case.get("source_policy") == "authoritative_only":
        return "authoritative"
    return "all_active"


def _evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    # Deliberately call retrieve_context instead of RagService.retrieve_only:
    # the latter may invoke LLM query rewriting for history-dependent prompts.
    try:
        _context, _sources, raw_results = retrieve_context(
            str(case["query"]),
            return_raw=True,
            retrieval_policy=_retrieval_policy_for_case(case),
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
    source_policy = str(case.get("source_policy") or "authoritative_only")
    source_groups = case.get("source_groups") or []
    expected_evidence = case.get("expected_evidence", [])
    is_refusal = not bool(raw_results)
    recalls: dict[str, float | None] = {}
    for k in K_VALUES:
        if case["expected_refusal"]:
            recalls[str(k)] = None
        elif expected or source_groups:
            recalls[str(k)] = _source_policy_score(
                raw_results,
                k,
                policy=source_policy,
                expected_sources=case["expected_sources"],
                source_groups=source_groups,
            )
        else:
            recalls[str(k)] = None

    reciprocal_rank = 0.0
    expected_for_rank = _source_policy_sources(
        policy=source_policy,
        expected_sources=case["expected_sources"],
        source_groups=source_groups,
    )
    if expected_for_rank:
        for index, source in enumerate(ranked_sources, 1):
            if source in expected_for_rank:
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
        "source_policy": source_policy,
        "expected_sources": case["expected_sources"],
        "source_groups": source_groups,
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
            str(k): _evidence_at(
                raw_results,
                k,
                expected_evidence,
                policy=source_policy,
                source_groups=source_groups,
            )
            for k in K_VALUES
        },
        "evidence_reciprocal_rank": evidence_reciprocal_rank,
    }


def evaluate(cases_path: Path) -> dict[str, Any]:
    cases, cases_sha256 = _load_cases(cases_path)
    corpus_snapshot = _corpus_snapshot()
    results = [_evaluate_case(case) for case in cases]
    answerable = [item for item in results if not item["expected_refusal"] and item["status"] == "ok"]
    refusal_cases = [item for item in results if item["expected_refusal"] and item["status"] == "ok"]
    metrics: dict[str, Any] = {}
    for k in K_VALUES:
        values = [item["recall"][str(k)] for item in answerable if item["recall"][str(k)] is not None]
        metrics[f"recall_at_{k}"] = sum(values) / len(values) if values else None
    reciprocal_ranks = [item["reciprocal_rank"] for item in answerable]
    metrics["mrr"] = sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else None
    policy_metric_names = {
        "authoritative_only": "authoritative_recall",
        "all_required": "multi_document_coverage",
        "any_equivalent": "any_equivalent_hit_rate",
    }
    for policy, metric_name in policy_metric_names.items():
        policy_cases = [item for item in answerable if item.get("source_policy") == policy]
        for k in K_VALUES:
            values = [
                item["recall"][str(k)]
                for item in policy_cases
                if item["recall"][str(k)] is not None
            ]
            metrics[f"{metric_name}_at_{k}"] = (
                sum(values) / len(values) if values else None
            )
    metrics["source_policy_counts"] = dict(
        Counter(item.get("source_policy", "unknown") for item in answerable)
    )
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
        "evaluation_profile": EVALUATION_PROFILE,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "cases_sha256": cases_sha256,
        "corpus_snapshot": corpus_snapshot,
        "config": {
            "retrieval_version": RETRIEVAL_VERSION,
            "model_version": MODEL_VERSION,
            "embedding_model": MODEL_NAME,
            "reranker_model": RERANKER_MODEL,
            "rerank_score_threshold": RERANK_SCORE_THRESHOLD,
            "hybrid_alpha": HYBRID_ALPHA,
            "final_top_k": FINAL_TOP_K,
            "embedding_batch_size": EMBEDDING_BATCH_SIZE,
            "document_governance_policy": retrieval_policy(),
            "document_governance_sha256": _file_sha256(DOCUMENT_GOVERNANCE_PATH),
        },
        "metrics": metrics,
        "cases": results,
    }


def _file_sha256(path: Path) -> str | None:
    """Record the exact governance policy used by an offline baseline."""
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _corpus_snapshot() -> dict[str, Any]:
    """Bind an evaluation result to the active manifest content and revision."""
    payload = get_manifest().fingerprint_snapshot()
    sources = payload.get("sources") or []
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "manifest_generation": int(payload.get("generation") or 0),
        "manifest_initialized": bool(payload.get("initialized")),
        "source_count": len(sources),
        "chunk_count": sum(int(item.get("chunk_count") or 0) for item in sources),
        "sources": sources,
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def compare_baseline(report: dict[str, Any], baseline_path: Path) -> dict[str, Any]:
    """Refuse metric comparison when the active corpus differs from baseline."""
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"compatible": False, "reason": f"baseline_unreadable:{type(exc).__name__}"}
    expected = (
        baseline.get("corpus_snapshot", {}).get("sha256")
        if isinstance(baseline, dict)
        else None
    )
    actual = report.get("corpus_snapshot", {}).get("sha256")
    if not expected:
        return {"compatible": False, "reason": "baseline_missing_corpus_fingerprint"}
    if expected != actual:
        return {"compatible": False, "reason": "corpus_fingerprint_mismatch"}
    return {"compatible": True, "reason": "ok"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--baseline",
        type=Path,
        help="require a matching corpus fingerprint; mismatch exits with code 3",
    )
    args = parser.parse_args()
    report = evaluate(args.cases)
    comparison = None
    if args.baseline:
        comparison = compare_baseline(report, args.baseline)
        report["baseline_comparison"] = {"path": str(args.baseline), **comparison}
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if comparison is None or comparison["compatible"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
