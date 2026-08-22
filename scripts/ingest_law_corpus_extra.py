# -*- coding: utf-8 -*-
"""批量导入律所扩充语料：合同编通则司法解释、公司法、劳动合同法。

每个文档注册 document_governance（authoritative + control 证据包）与
document_acl，再调用 add_document_to_kb 完成入库与 manifest 原子激活。

用法：uv run python scripts/ingest_law_corpus_extra.py
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
        "source": "民法典合同编通则司法解释.md",
        "file": "corpus/民法典合同编通则司法解释.md",
        "document_family": "contract_interpretation",
        "version": "2023-12-05",
        "effective_from": "2023-12-05",
        "classification": "technical",
        "department": "legal",
        "control": {
            "issuing_department": "最高人民法院",
            "approver": "最高人民法院审判委员会",
            "approval_reference": "法释〔2023〕13号，2023年5月23日最高人民法院审判委员会第1889次会议通过",
            "notice_reference": "最高人民法院公告，2023年12月4日公布",
            "replacement_decision": "does_not_replace",
            "conflict_priority": "对民法典合同编通则的司法解释，与民法典合同编配套适用",
            "evidence_refs": ["https://www.court.gov.cn/fabu/xiangqing/419382.html"],
            "scope": {"legal_entities": ["all"], "regions": ["中华人民共和国"], "employee_types": ["all"]},
        },
    },
    {
        "source": "公司法.md",
        "file": "corpus/公司法.md",
        "document_family": "company_law",
        "version": "2023-12-29",
        "effective_from": "2024-07-01",
        "classification": "technical",
        "department": "legal",
        "control": {
            "issuing_department": "全国人民代表大会常务委员会",
            "approver": "全国人民代表大会常务委员会",
            "approval_reference": "2023年12月29日第十四届全国人大常委会第七次会议第二次修订",
            "notice_reference": "中华人民共和国主席令第十五号",
            "replacement_decision": "does_not_replace",
            "conflict_priority": "公司法为本知识库公司领域唯一权威法律来源",
            "evidence_refs": ["https://paper.people.com.cn/rmrb/html/2024-01/02/nw.D110000renmrb_20240102_1-13.htm"],
            "scope": {"legal_entities": ["all"], "regions": ["中华人民共和国"], "employee_types": ["all"]},
        },
    },
    {
        "source": "劳动合同法.md",
        "file": "corpus/劳动合同法.md",
        "document_family": "labour_contract_law",
        "version": "2012-12-28",
        "effective_from": "2013-07-01",
        "classification": "technical",
        "department": "legal",
        "control": {
            "issuing_department": "全国人民代表大会常务委员会",
            "approver": "全国人民代表大会常务委员会",
            "approval_reference": "2007年6月29日通过，2012年12月28日修正",
            "notice_reference": "中华人民共和国主席令",
            "replacement_decision": "does_not_replace",
            "conflict_priority": "劳动合同法为本知识库劳动领域唯一权威法律来源",
            "evidence_refs": ["http://www.npc.gov.cn/npc/c1773/c2518/c12898/201905/t20190523_46320.html"],
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

    print("全部导入完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
