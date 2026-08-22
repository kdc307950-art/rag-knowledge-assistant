# -*- coding: utf-8 -*-
"""导入行政/刑事扩充语料：行政处罚法、行政复议法、刑诉法解释（法释〔2021〕1号）。

governance/acl 为增量更新（读现有 json 再写回），不影响已导入文档。

用法：uv run python scripts/ingest_xzfa.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from enterprise_rag.storage.vector_store import add_document_to_kb  # noqa: E402

GOVERNANCE_PATH = PROJECT_ROOT / "config" / "document_governance.json"
ACL_PATH = PROJECT_ROOT / "config" / "document_acl.json"

DOCUMENTS = [
    {
        "source": "行政处罚法.md",
        "file": "corpus/行政处罚法.md",
        "document_family": "administrative_penalty_law",
        "version": "2021-01-22",
        "effective_from": "2021-07-15",
        "classification": "technical",
        "department": "legal",
        "control": {
            "issuing_department": "全国人民代表大会常务委员会",
            "approver": "全国人民代表大会常务委员会",
            "approval_reference": "2021年1月22日第十三届全国人大常委会第二十五次会议修订",
            "notice_reference": "中华人民共和国主席令",
            "replacement_decision": "does_not_replace",
            "conflict_priority": "行政处罚法为本知识库行政处罚领域唯一权威法律来源",
            "evidence_refs": ["https://fgk.chinatax.gov.cn/zcfgk/c100009/c5211778/content.html"],
            "scope": {"legal_entities": ["all"], "regions": ["中华人民共和国"], "employee_types": ["all"]},
        },
    },
    {
        "source": "行政复议法.md",
        "file": "corpus/行政复议法.md",
        "document_family": "administrative_reconsideration_law",
        "version": "2023-09-01",
        "effective_from": "2024-01-01",
        "classification": "technical",
        "department": "legal",
        "control": {
            "issuing_department": "全国人民代表大会常务委员会",
            "approver": "全国人民代表大会常务委员会",
            "approval_reference": "2023年9月1日第十四届全国人大常委会第五次会议修订",
            "notice_reference": "中华人民共和国主席令",
            "replacement_decision": "does_not_replace",
            "conflict_priority": "行政复议法为本知识库行政复议领域唯一权威法律来源",
            "evidence_refs": ["https://www.mee.gov.cn/ywgz/fgbz/fl/202312/t20231205_1058240.shtml"],
            "scope": {"legal_entities": ["all"], "regions": ["中华人民共和国"], "employee_types": ["all"]},
        },
    },
    {
        "source": "刑诉法解释.md",
        "file": "corpus/刑诉法解释.md",
        "document_family": "criminal_procedure_interpretation",
        "version": "2021-02-04",
        "effective_from": "2021-03-01",
        "classification": "technical",
        "department": "legal",
        "control": {
            "issuing_department": "最高人民法院",
            "approver": "最高人民法院审判委员会",
            "approval_reference": "法释〔2021〕1号，2020年12月7日最高人民法院审判委员会第1820次会议通过，替代法释〔2012〕21号",
            "notice_reference": "最高人民法院公告",
            "replacement_decision": "does_not_replace",
            "conflict_priority": "刑诉法解释为刑事诉讼审判程序领域唯一权威司法解释，与刑事诉讼法配套适用",
            "evidence_refs": ["http://www.court.gov.cn/fabu/xiangqing/286491.html"],
            "scope": {"legal_entities": ["all"], "regions": ["中华人民共和国"], "employee_types": ["all"]},
        },
    },
]


def _governance_entry(doc: dict) -> dict:
    return {
        "document_family": doc["document_family"],
        "version": doc["version"],
        "effective_from": doc["effective_from"],
        "effective_to": None,
        "authority_level": "authoritative",
        "retrieval_status": "active",
        "control": doc["control"],
        "supersedes": doc["control"].get("supersedes", []),
    }


def _acl_entry(doc: dict) -> dict:
    return {
        "classification": doc["classification"],
        "department": doc["department"],
        "visibility": "all",
        "owner_id": "",
        "basis": f"{doc['source']}，公开法律/司法解释文本，面向全所。",
    }


def main() -> int:
    gov = json.loads(GOVERNANCE_PATH.read_text(encoding="utf-8"))
    acl = json.loads(ACL_PATH.read_text(encoding="utf-8"))

    for doc in DOCUMENTS:
        name = doc["source"]
        gov["documents"][name] = _governance_entry(doc)
        acl["documents"][name] = _acl_entry(doc)
        print(f"[配置] 注册 {name} (authoritative, legal/all)")

    GOVERNANCE_PATH.write_text(json.dumps(gov, ensure_ascii=False, indent=2), encoding="utf-8")
    ACL_PATH.write_text(json.dumps(acl, ensure_ascii=False, indent=2), encoding="utf-8")

    for doc in DOCUMENTS:
        name = doc["source"]
        content = (PROJECT_ROOT / doc["file"]).read_text(encoding="utf-8")
        print(f"[导入] {name}，正文 {len(content)} 字符 ...")
        result = add_document_to_kb(
            file_name=name,
            content=content,
            metadata={
                "classification": doc["classification"],
                "department": doc["department"],
                "visibility": "all",
            },
            refresh_indexes=True,
        )
        print(f"  结果: {result}")

    print("导入完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
