# -*- coding: utf-8 -*-
"""调试单条 query 的检索：打印术语扩展结果与召回/重排详情。

用法：uv run python scripts/debug_query.py "缔约过失责任的情形包括哪些？"
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from enterprise_rag.rag.retriever import retrieve_context  # noqa: E402
from enterprise_rag.rag.query_expansion import expand_query  # noqa: E402


def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else "缔约过失责任的情形包括哪些？"
    print("原 query :", query)
    print("扩展后   :", expand_query(query))
    print("-" * 60)
    _context, sources, raw = retrieve_context(query, return_raw=True)
    print("召回结果数:", len(raw))
    if not raw:
        print("=> 拒答（无片段通过 rerank 阈值）")
    else:
        for r in raw[:5]:
            pid = str(r.get("parent_id") or "")
            print(
                f"  score={r['rerank_score']:.4f} "
                f"chapter={r.get('chapter')} "
                f"parent=...{pid[-8:]}"
            )
        print("来源:", sources[:3])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
