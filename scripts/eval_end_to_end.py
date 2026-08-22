# -*- coding: utf-8 -*-
"""端到端问答评测：query → 检索 → LLM 生成 → 判分。

判分维度：
1. 法条编号命中（规则）：回答是否包含 expected_evidence 对应条款号（对跨文档用例要求全部命中）
2. Groundedness 内容支持（LLM-as-judge）：回答法律主张是否被检索 context 支持
3. 拒答正确性（规则）：拒答用例是否被正确拒答

可复现性：生成与判分均 temperature=0。
成本：可答用例每次 = 1 次生成 + 1 次判分；拒答用例 = 1 次生成。

用法：
  uv run python scripts/eval_end_to_end.py --cases eval/retrieval_cases_law.jsonl --output eval/results/e2e-2026-08-22.json [--limit N]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import chromadb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from enterprise_rag.config import KB_DATA_DIR, LLM_MODEL  # noqa: E402
from enterprise_rag.llm.client import get_llm  # noqa: E402
from enterprise_rag.llm.prompts import SYSTEM_PROMPT_TEMPLATE  # noqa: E402
from enterprise_rag.rag.retriever import retrieve_context  # noqa: E402

JUDGE_PROMPT = """你是法律 RAG 系统的质量评审员。以下是一次检索问答的【检索上下文】和【系统回答】。

【检索上下文】
{context}

【系统回答】
{answer}

任务：判断系统回答中的法律主张是否被检索上下文支持。
- supported：回答的所有关键法律主张都能在上下文中找到明确依据；
- partial：部分主张有依据，部分主张超出上下文或无依据；
- unsupported：回答存在关键法律主张但上下文无法支持，或与上下文矛盾。

只输出 JSON，不要输出其他内容：{{"verdict": "supported"|"partial"|"unsupported", "reason": "一句话原因"}}"""

E2E_GENERATE_PROMPT = """你是一个律所法律知识库助手，请使用中文回答。

规则：
1. 仅使用下方提供的参考资料，不得编造或引入外部知识。
2. 回答必须明确指出所依据的具体法条编号（如「民法典第五百八十五条」「刑事诉讼法第三十九条」）。
3. 对每个关键事实，在对应句末引用一个或多个资料标识，例如 [S1]。
4. 如果参考资料无法回答，请明确说明。
5. 只输出 Markdown。

参考资料：
{context}
"""

ARTICLE_RE = re.compile(r"第([零一二三四五六七八九十百千]+)条")


def extract_article_no(text: str) -> str | None:
    m = ARTICLE_RE.match(text.strip())
    return m.group(0) if m else None


def load_parent_text_map() -> dict[str, str]:
    client = chromadb.PersistentClient(path=str(KB_DATA_DIR))
    col = client.get_collection("kb_text")
    res = col.get(include=["metadatas"])
    mapping: dict[str, str] = {}
    for meta in res.get("metadatas") or []:
        pid = str(meta.get("parent_id") or "")
        if pid and pid not in mapping:
            mapping[pid] = str(meta.get("parent_text") or "")
    return mapping


def call_llm(messages: list[dict]) -> str:
    client = get_llm()
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        temperature=0,
        stream=False,
        timeout=120,
    )
    return resp.choices[0].message.content or ""


def parse_judge(text: str) -> tuple[str, str]:
    m = re.search(r"\{\"verdict\"\s*:\s*\"(\w+)\"[^}]*\"reason\"\s*:\s*\"([^\"]*)\"", text)
    if m:
        return m.group(1), m.group(2)
    if "supported" in text:
        return "supported", text[:80]
    if "partial" in text:
        return "partial", text[:80]
    return "unsupported", text[:80]


def is_refusal(answer: str) -> bool:
    """拒答判定：回答不含任何条款号且表达无法回答。"""
    if ARTICLE_RE.search(answer):
        return False
    refusal_markers = ("无法", "不能回答", "未收录", "没有找到", "抱歉", "知识库中", "没有相关资料", "无法回答")
    return any(marker in answer for marker in refusal_markers)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    cases = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        cases = cases[: args.limit]

    results = []
    for case in cases:
        case_id = case["case_id"]
        query = case["query"]
        expected_refusal = bool(case.get("expected_refusal", False))

        # 1. 检索（含术语扩展，与项目生产链路一致）
        try:
            context, sources, raw = retrieve_context(query, return_raw=True)
        except Exception as exc:  # noqa: BLE001
            results.append({"case_id": case_id, "status": "error", "error": str(exc)})
            continue

        # 2. 生成（greedy，可复现；评测 prompt 强调必须引用具体法条编号）
        system_prompt = E2E_GENERATE_PROMPT.format(context=context or "（无检索结果）")
        try:
            answer = call_llm([{"role": "system", "content": system_prompt}, {"role": "user", "content": query}])
        except Exception as exc:  # noqa: BLE001
            results.append({"case_id": case_id, "status": "error", "error": str(exc)})
            continue

        record: dict = {"case_id": case_id, "query": query, "answer": answer[:2000], "status": "ok"}

        if expected_refusal:
            record["expected_refusal"] = True
            record["actual_refusal"] = is_refusal(answer)
            record["refusal_ok"] = record["actual_refusal"]
            results.append(record)
            print(f"[{case_id}] 拒答={'正确' if record['refusal_ok'] else '误答'}")
            continue

        # 3a. 法条编号命中（规则，用黄金集预存的目标条款号）
        expected_articles = [
            {"source": ev.get("source"), "article": ev.get("article")}
            for ev in case.get("expected_evidence", [])
            if ev.get("article")
        ]
        hit = [a["article"] in answer for a in expected_articles]
        # any_equivalent 任一命中即可；all_required/单文档要求全部命中
        policy = case.get("source_policy", "authoritative_only")
        record["expected_articles"] = expected_articles
        record["article_hit"] = (any(hit) if policy == "any_equivalent" else all(hit)) if hit else False
        record["article_hit_detail"] = hit

        # 3b. Groundedness（LLM-as-judge）
        if context:
            try:
                judge_text = call_llm(
                    [
                        {"role": "system", "content": JUDGE_PROMPT.format(context=context[:6000], answer=answer[:2000])},
                        {"role": "user", "content": "请给出评审结论。"},
                    ]
                )
                verdict, reason = parse_judge(judge_text)
            except Exception as exc:  # noqa: BLE001
                verdict, reason = "error", str(exc)[:120]
        else:
            verdict, reason = "no_context", "检索无结果但用例期望可答"

        record["groundedness"] = verdict
        record["groundedness_reason"] = reason
        record["e2e_pass"] = record["article_hit"] and verdict in ("supported", "partial")
        results.append(record)
        print(f"[{case_id}] 条款命中={record['article_hit']} groundedness={verdict} e2e={'PASS' if record['e2e_pass'] else 'FAIL'}")

    # 4. 汇总
    answerable = [r for r in results if r.get("status") == "ok" and not r.get("expected_refusal")]
    refusals = [r for r in results if r.get("status") == "ok" and r.get("expected_refusal")]
    summary = {
        "profile": "e2e-v1",
        "model": LLM_MODEL,
        "case_count": len(cases),
        "answerable_count": len(answerable),
        "refusal_count": len(refusals),
        "error_count": len([r for r in results if r.get("status") == "error"]),
        "article_hit_rate": round(sum(1 for r in answerable if r["article_hit"]) / len(answerable), 4) if answerable else None,
        "groundedness_supported_rate": round(
            sum(1 for r in answerable if r["groundedness"] == "supported") / len(answerable), 4
        ) if answerable else None,
        "groundedness_partial_rate": round(
            sum(1 for r in answerable if r["groundedness"] == "partial") / len(answerable), 4
        ) if answerable else None,
        "groundedness_unsupported_rate": round(
            sum(1 for r in answerable if r["groundedness"] == "unsupported") / len(answerable), 4
        ) if answerable else None,
        "e2e_pass_rate": round(sum(1 for r in answerable if r["e2e_pass"]) / len(answerable), 4) if answerable else None,
        "refusal_accuracy": round(sum(1 for r in refusals if r["refusal_ok"]) / len(refusals), 4) if refusals else None,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"summary": summary, "cases": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== 汇总 ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"\n输出: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
