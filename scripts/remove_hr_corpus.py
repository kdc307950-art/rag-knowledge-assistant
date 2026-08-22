# -*- coding: utf-8 -*-
"""清理员工手册 demo 语料，只保留民法典合同编（律所落地基线）。

删除 Chroma 分块 + manifest 逻辑条目 + governance/acl 配置条目。
用法：uv run python scripts/remove_hr_corpus.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from enterprise_rag.storage.vector_store import delete_document_authorized  # noqa: E402

HR_SOURCES = [
    "2025版新劳动合同法下的企业员工手册.md",
    "企业员工手册（2025版）.md",
]
GOVERNANCE_PATH = PROJECT_ROOT / "config" / "document_governance.json"
ACL_PATH = PROJECT_ROOT / "config" / "document_acl.json"


def remove_hr_documents() -> None:
    for name in HR_SOURCES:
        result = delete_document_authorized(name)
        status = "已删除" if result is not None else "不存在（跳过）"
        print(f"[Chroma/manifest] {name}: {status}")


def cleanup_governance() -> None:
    gov = json.loads(GOVERNANCE_PATH.read_text(encoding="utf-8"))
    removed = []
    for name in HR_SOURCES:
        if name in gov.get("documents", {}):
            del gov["documents"][name]
            removed.append(name)
    # 保留 notes 中与民法典无关的说明可能引起混淆，删除仅针对手册的 notes 由人工处理；
    # 这里只删 documents 条目，notes 保留不动。
    GOVERNANCE_PATH.write_text(json.dumps(gov, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[governance] 删除条目: {removed or '无'}")


def cleanup_acl() -> None:
    acl = json.loads(ACL_PATH.read_text(encoding="utf-8"))
    removed = []
    for name in HR_SOURCES:
        if name in acl.get("documents", {}):
            del acl["documents"][name]
            removed.append(name)
    ACL_PATH.write_text(json.dumps(acl, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[acl] 删除条目: {removed or '无'}")


if __name__ == "__main__":
    remove_hr_documents()
    cleanup_governance()
    cleanup_acl()
    print("清理完成。")
