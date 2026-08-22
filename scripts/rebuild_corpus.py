# -*- coding: utf-8 -*-
"""用当前分块逻辑重建知识库：删除全部 source 后从 corpus/ 重新导入。

用于分块策略变更（如 split_by_chapters 正则修复）后的全量重建。
corpus/ 源文件、黄金集、governance/acl 配置均保留，只重建 Chroma + manifest + BM25。
用法：uv run python scripts/rebuild_corpus.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from enterprise_rag.storage.vector_store import add_document_to_kb, delete_document_authorized  # noqa: E402

CORPUS = PROJECT_ROOT / "corpus"
SOURCES = sorted(p.name for p in CORPUS.glob("*.md"))


def main() -> int:
    print(f"发现 {len(SOURCES)} 个语料: {SOURCES}")
    print("=== 1/2 删除现有 source（仅索引，corpus/ 源文件与配置保留）===")
    for name in SOURCES:
        try:
            delete_document_authorized(name, access_context=None)
            print(f"[删除] {name}")
        except Exception as exc:  # noqa: BLE001
            print(f"[删除失败] {name}: {exc}")

    print("=== 2/2 按新分块逻辑重新导入 ===")
    for name in SOURCES:
        content = (CORPUS / name).read_text(encoding="utf-8")
        result = add_document_to_kb(
            file_name=name,
            content=content,
            metadata={"classification": "technical", "department": "legal", "visibility": "all"},
            refresh_indexes=True,
        )
        print(f"[导入] {name}: {result}")

    print("重建完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
