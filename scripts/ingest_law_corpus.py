# -*- coding: utf-8 -*-
"""将《民法典·合同编》作为权威法律语料导入知识库。

注册 document_governance（authoritative + control 证据包）与 document_acl，
再调用 add_document_to_kb 完成分块、向量化、写 Chroma 与 manifest 原子激活。

用法：uv run python scripts/ingest_law_corpus.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from enterprise_rag.storage.vector_store import add_document_to_kb  # noqa: E402

SOURCE_NAME = "民法典合同编.md"
CORPUS_FILE = PROJECT_ROOT / "corpus" / "民法典_合同编.md"
GOVERNANCE_PATH = PROJECT_ROOT / "config" / "document_governance.json"
ACL_PATH = PROJECT_ROOT / "config" / "document_acl.json"


def update_governance() -> None:
    gov = json.loads(GOVERNANCE_PATH.read_text(encoding="utf-8"))
    gov["documents"][SOURCE_NAME] = {
        "document_family": "civil_code_contract",
        "version": "2020-05-28",
        "effective_from": "2021-01-01",
        "effective_to": None,
        "authority_level": "authoritative",
        "retrieval_status": "active",
        "control": {
            "issuing_department": "全国人民代表大会",
            "approver": "全国人民代表大会",
            "approval_reference": "第十三届全国人民代表大会第三次会议于2020年5月28日通过",
            "notice_reference": "中华人民共和国主席令（第四十五号）",
            "replacement_decision": "does_not_replace",
            "conflict_priority": "民法典合同编为本知识库合同领域唯一权威法律来源",
            "evidence_refs": [
                "https://flk.npc.gov.cn/detail2.html?ZmY4MDgwODE3MjlkMWVmZTAxNzI5ZDUwYjVjNTAwYmY"
            ],
            "scope": {
                "legal_entities": ["all"],
                "regions": ["中华人民共和国"],
                "employee_types": ["all"],
            },
        },
        "supersedes": [],
    }
    GOVERNANCE_PATH.write_text(
        json.dumps(gov, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[1/3] 已更新 {GOVERNANCE_PATH.name}: 注册 {SOURCE_NAME} (authoritative)")


def update_acl() -> None:
    acl = json.loads(ACL_PATH.read_text(encoding="utf-8"))
    acl["documents"][SOURCE_NAME] = {
        "classification": "technical",
        "department": "legal",
        "visibility": "all",
        "owner_id": "",
        "basis": "《中华人民共和国民法典》合同编，全国人民代表大会通过，2021-01-01 施行，公开法律文本，面向全所。",
    }
    ACL_PATH.write_text(json.dumps(acl, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[2/3] 已更新 {ACL_PATH.name}: 注册 {SOURCE_NAME} (legal/all)")


def ingest() -> None:
    content = CORPUS_FILE.read_text(encoding="utf-8")
    print(f"[3/3] 导入 {SOURCE_NAME}，正文 {len(content)} 字符 ...")
    result = add_document_to_kb(
        file_name=SOURCE_NAME,
        content=content,
        metadata={
            "classification": "technical",
            "department": "legal",
            "visibility": "all",
        },
        refresh_indexes=True,
    )
    print(f"导入结果: {result}")


if __name__ == "__main__":
    update_governance()
    update_acl()
    ingest()
    print("完成。")
