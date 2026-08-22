# -*- coding: utf-8 -*-
"""导入刑事法律语料：刑法、刑事诉讼法。

governance/acl 为增量更新（读现有 json 再写回），不影响已导入文档。

用法：uv run python scripts/ingest_criminal_law.py
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
        "source": "刑法.md",
        "file": "corpus/刑法.md",
        "document_family": "criminal_law",
        "version": "2023-12-29",
        "effective_from": "2024-03-01",
        "classification": "technical",
        "department": "legal",
        "control": {
            "issuing_department": "全国人民代表大会",
            "approver": "全国人民代表大会",
            "approval_reference": "1997年3月14日第八届全国人大第五次会议修订，2023年12月29日刑法修正案（十二）修正",
            "notice_reference": "中华人民共和国主席令",
            "replacement_decision": "does_not_replace",
            "conflict_priority": "刑法为本知识库刑事领域唯一权威法律来源",
            "evidence_refs": ["http://www.chnlawyer.net/law/subs/xingfa.html"],
            "scope": {"legal_entities": ["all"], "regions": ["中华人民共和国"], "employee_types": ["all"]},
        },
    },
    {
        "source": "刑事诉讼法.md",
        "file": "corpus/刑事诉讼法.md",
        "document_family": "criminal_procedure_law",
        "version": "2018-10-26",
        "effective_from": "2018-10-26",
        "classification": "technical",
        "department": "legal",
        "control": {
            "issuing_department": "全国人民代表大会",
            "approver": "全国人民代表大会",
            "approval_reference": "2018年10月26日第十三届全国人大常委会第六次会议第三次修正",
            "notice_reference": "中华人民共和国主席令",
            "replacement_decision": "does_not_replace",
            "conflict_priority": "刑事诉讼法为本知识库刑事诉讼程序领域唯一权威法律来源",
            "evidence_refs": ["https://www.xinhuanet.com/politics/2018-10/27/c_1123620821_9.htm"],
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
        "supersedes": [],
    }


def _acl_entry(doc: dict) -> dict:
    return {
        "classification": doc["classification"],
        "department": doc["department"],
        "visibility": "all",
        "owner_id": "",
        "basis": f"{doc['source']}，公开法律文本，面向全所。",
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

    print("刑事法律导入完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
