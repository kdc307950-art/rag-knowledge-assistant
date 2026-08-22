# -*- coding: utf-8 -*-
"""为律所知识库生成黄金集（schema v2，多 source）。

覆盖：民法典合同编、合同编通则司法解释、公司法、劳动合同法，
以及「法律+司法解释」跨文档用例与合同编外严格拒答用例。

用法：uv run python scripts/build_law_golden_set.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import chromadb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from enterprise_rag.config import KB_DATA_DIR  # noqa: E402

OUT = PROJECT_ROOT / "eval" / "retrieval_cases_law.jsonl"

MFC = "民法典合同编.md"           # 民法典合同编
SFJS = "民法典合同编通则司法解释.md"  # 合同编通则司法解释
GSF = "公司法.md"                # 公司法
LDHT = "劳动合同法.md"           # 劳动合同法
XF = "刑法.md"                   # 刑法
XSSF = "刑事诉讼法.md"           # 刑事诉讼法
XZCF = "行政处罚法.md"           # 行政处罚法
XZFYY = "行政复议法.md"          # 行政复议法
XSJS = "刑诉法解释.md"           # 刑诉法解释（法释〔2021〕1号）

# (case_id, query, [(source, 目标法条编号), ...], tags, expected_refusal)
CASES = [
    # —— 民法典合同编（精确法条 + 术语）——
    ("law_001", "合同可以采用哪些形式订立？", [(MFC, "第四百六十九条")], ["law", "factual", "easy"], False),
    ("law_002", "格式条款在哪些情形下无效？", [(MFC, "第四百九十七条")], ["law", "factual", "medium"], False),
    ("law_003", "约定的违约金过分高于造成的损失，应当如何处理？", [(MFC, "第五百八十五条"), (SFJS, "第六十五条")], ["law", "factual", "easy", "equivalent"], False),
    ("law_004", "定金的数额不得超过主合同标的额的多少？", [(MFC, "第五百八十六条")], ["law", "factual", "easy"], False),
    ("law_005", "当事人可以解除合同的法定情形有哪些？", [(MFC, "第五百六十三条")], ["law", "factual", "medium"], False),
    ("law_006", "合同成立后基础条件发生重大变化，受不利影响的一方可以怎么办？", [(MFC, "第五百三十三条")], ["law", "factual", "hard"], False),
    ("law_007", "缔约过失责任的情形包括哪些？", [(MFC, "第五百条")], ["law", "factual", "medium"], False),
    ("law_008", "保证期间没有约定或者约定不明确的，保证期间是多长？", [(MFC, "第六百九十二条")], ["law", "factual", "medium"], False),
    ("law_009", "租赁合同约定的租赁期限最长不得超过多少年？", [(MFC, "第七百零五条")], ["law", "factual", "easy"], False),
    ("law_010", "发包人逾期不支付建设工程价款，承包人可以行使什么权利？", [(MFC, "第八百零七条")], ["law", "factual", "medium"], False),
    ("law_011", "借款的利息可以预先在本金中扣除吗？", [(MFC, "第六百七十条")], ["law", "factual", "easy"], False),
    ("law_012", "债权人转让债权未通知债务人，该转让对债务人发生效力吗？", [(MFC, "第五百四十六条")], ["law", "factual", "medium"], False),
    ("law_013", "违约损失赔偿额应当如何确定？", [(MFC, "第五百八十四条")], ["law", "factual", "medium"], False),
    ("law_014", "赠与人可以撤销赠与的情形有哪些？", [(MFC, "第六百六十三条")], ["law", "factual", "medium"], False),
    ("law_015", "分期付款买受人未支付到期价款达到全部价款的多少，出卖人可以解除合同？", [(MFC, "第六百三十四条")], ["law", "factual", "hard"], False),
    ("law_016", "什么是格式条款？", [(MFC, "第四百九十六条")], ["law", "proper_noun", "easy"], False),
    ("law_017", "什么是保证期间？", [(MFC, "第六百九十二条")], ["law", "proper_noun", "medium"], False),
    ("law_018", "试用买卖的试用期届满，买受人对是否购买未作表示的，效力如何？", [(MFC, "第六百三十八条")], ["law", "reasoning", "hard"], False),
    # —— 合同编通则司法解释 ——
    ("sifa_001", "合同条款的解释应当考虑哪些因素？", [(SFJS, "第一条")], ["law", "factual", "medium"], False),
    ("sifa_002", "民法典所称的「交易习惯」如何认定？", [(SFJS, "第二条")], ["law", "factual", "medium"], False),
    ("sifa_003", "预约合同在什么情况下可以认定成立？", [(SFJS, "第六条")], ["law", "factual", "medium"], False),
    ("sifa_004", "当事人对合同是否成立存在争议，人民法院如何处理？", [(SFJS, "第三条")], ["law", "factual", "medium"], False),
    # —— 公司法 ——
    ("gongsi_001", "有限责任公司的股东人数上限是多少？", [(GSF, "第四十二条")], ["law", "factual", "easy"], False),
    ("gongsi_002", "全体股东认缴的出资额应当在多长时间内缴足？", [(GSF, "第四十七条")], ["law", "factual", "medium"], False),
    ("gongsi_003", "公司分配当年税后利润时应提取多少法定公积金？", [(GSF, "第二百一十条")], ["law", "factual", "medium"], False),
    ("gongsi_004", "公司股东滥用法人独立地位逃避债务的，承担什么责任？", [(GSF, "第二十三条")], ["law", "factual", "medium"], False),
    # —— 劳动合同法 ——
    ("laodong_001", "劳动合同试用期最长不得超过多久？", [(LDHT, "第十九条")], ["law", "factual", "easy"], False),
    ("laodong_002", "用人单位应当向劳动者支付经济补偿的情形有哪些？", [(LDHT, "第四十六条")], ["law", "factual", "medium"], False),
    ("laodong_003", "什么情形下应当订立无固定期限劳动合同？", [(LDHT, "第十四条")], ["law", "factual", "medium"], False),
    ("laodong_004", "用人单位可以与劳动者约定由劳动者承担违约金的情形？", [(LDHT, "第二十二条")], ["law", "factual", "hard"], False),
    # —— 跨文档：法律 + 司法解释 ——
    ("cross_001", "合同条款的解释应当遵循什么规则？", [(MFC, "第四百六十六条"), (SFJS, "第一条")], ["law", "cross_document", "hard"], False),
    ("cross_002", "情势变更如何认定与处理？", [(MFC, "第五百三十三条"), (SFJS, "第三十二条")], ["law", "cross_document", "hard"], False),
    # —— 刑法 ——
    ("xingfa_001", "故意伤害他人身体的，如何量刑？", [(XF, "第二百三十四条")], ["law", "criminal", "factual", "easy"], False),
    ("xingfa_002", "盗窃公私财物数额较大的，如何处罚？", [(XF, "第二百六十四条")], ["law", "criminal", "factual", "easy"], False),
    ("xingfa_003", "正当防卫造成不法侵害人损害的情形，负刑事责任吗？", [(XF, "第二十条")], ["law", "criminal", "factual", "medium"], False),
    ("xingfa_004", "诈骗公私财物数额较大的，如何量刑？", [(XF, "第二百六十六条")], ["law", "criminal", "factual", "easy"], False),
    # —— 刑事诉讼法 ——
    ("susong_001", "刑事案件的侦查、提起公诉、审判分别由哪些机关负责？", [(XSSF, "第三条")], ["law", "criminal", "factual", "medium"], False),
    ("susong_002", "可以用于证明案件事实的材料有哪些？", [(XSSF, "第五十条")], ["law", "criminal", "factual", "medium"], False),
    ("susong_003", "侦查人员对与犯罪有关的场所、物品、人身、尸体应当如何处理？", [(XSSF, "第一百二十八条")], ["law", "criminal", "factual", "medium"], False),
    ("susong_004", "逮捕犯罪嫌疑人、被告人的条件是什么？", [(XSSF, "第八十一条")], ["law", "criminal", "factual", "medium"], False),
    # —— 行政处罚法 ——
    ("xzcf_001", "行政处罚的种类有哪些？", [(XZCF, "第九条")], ["law", "administrative", "factual", "easy"], False),
    ("xzcf_002", "行政处罚的定义？", [(XZCF, "第二条")], ["law", "administrative", "factual", "easy"], False),
    ("xzcf_003", "违法行为在几年内未被发现的，不再给予行政处罚？", [(XZCF, "第三十六条")], ["law", "administrative", "factual", "medium"], False),
    # —— 行政复议法 ——
    ("xzfuyi_001", "对行政处罚不服如何申请行政复议？", [(XZFYY, "第二条")], ["law", "administrative", "factual", "medium"], False),
    ("xzfuyi_002", "申请行政复议的期限是多久？", [(XZFYY, "第二十条")], ["law", "administrative", "factual", "medium"], False),
    ("xzfuyi_003", "行政复议机关收到行政复议申请后应当如何处理？", [(XZFYY, "第三十条")], ["law", "administrative", "factual", "medium"], False),
    # —— 刑诉法解释 ——
    ("xsjs_001", "人民法院直接受理的自诉案件有哪些？", [(XSJS, "第一条")], ["law", "criminal", "factual", "medium"], False),
    ("xsjs_002", "辩护律师如何同在押的被告人会见和通信？", [(XSSF, "第三十九条"), (XSJS, "第五十六条")], ["law", "criminal", "factual", "medium", "equivalent"], False),
    # —— 跨文档：刑诉法 + 刑诉法解释 ——
    ("cross_003", "辩护律师可以同在押的犯罪嫌疑人、被告人会见和通信吗？", [(XSSF, "第三十九条"), (XSJS, "第五十六条")], ["law", "criminal", "cross_document", "hard"], False),
    # —— 知识库外严格拒答 ——
    ("refuse_002", "结婚的法定年龄是多少岁？", [], ["law", "refusal", "out_of_scope", "expected_refusal"], True),
    ("refuse_003", "法定继承的第一顺序继承人有哪些？", [], ["law", "refusal", "out_of_scope", "expected_refusal"], True),
    ("refuse_004", "专利侵权损害赔偿数额如何确定？", [], ["law", "refusal", "out_of_scope", "expected_refusal"], True),
    ("refuse_005", "商标侵权损害赔偿数额如何计算？", [], ["law", "refusal", "out_of_scope", "expected_refusal"], True),
    ("refuse_006", "个人所得税综合所得适用什么税率？", [], ["law", "refusal", "out_of_scope", "expected_refusal"], True),
]


def build_parent_map() -> dict[str, dict[str, str]]:
    client = chromadb.PersistentClient(path=str(KB_DATA_DIR))
    collection = client.get_collection("kb_text")
    res = collection.get(include=["metadatas", "documents"])
    source_map: dict[str, dict[str, str]] = {}
    for meta, doc in zip(res.get("metadatas") or [], res.get("documents") or []):
        source = str(meta.get("source") or "")
        pid = str(meta.get("parent_id") or "")
        if not source or not pid:
            continue
        parents = source_map.setdefault(source, {})
        if pid not in parents:
            parents[pid] = str(meta.get("parent_text") or doc or "")
    return source_map


def main() -> int:
    source_map = build_parent_map()
    print("各 source 父块数:", {s: len(p) for s, p in source_map.items()})

    lines: list[str] = []
    missing: list[str] = []
    for case_id, query, targets, tags, refusal in CASES:
        if refusal:
            lines.append(json.dumps({
                "schema_version": 2, "case_id": case_id, "query": query, "tags": tags,
                "expected_refusal": True, "expected_sources": [], "expected_evidence": [],
                "review_status": "approved",
            }, ensure_ascii=False))
            continue

        evidence = []
        ok = True
        for source, article in targets:
            parents = source_map.get(source, {})
            pid = next((p for p, text in parents.items() if article in text), None)
            if pid is None:
                missing.append(f"{case_id}: {source}/{article}")
                ok = False
                break
            evidence.append({"source": source, "parent_id": pid})
        if not ok:
            continue

        expected_sources = [s for s, _ in targets]
        line = {
            "schema_version": 2, "case_id": case_id, "query": query, "tags": tags,
            "expected_refusal": False,
            "expected_sources": expected_sources,
            "expected_evidence": evidence,
            "review_status": "approved",
        }
        if "equivalent" in tags:
            line["source_policy"] = "any_equivalent"
            line["source_groups"] = [expected_sources]
        elif len(expected_sources) > 1:
            line["source_policy"] = "all_required"
        lines.append(json.dumps(line, ensure_ascii=False))

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"生成 {len(lines)} 条用例 -> {OUT}")
    if missing:
        print("未找到 parent_id 的用例:")
        for item in missing:
            print("  ", item)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
