"""Export reviewed feedback candidates for manual dev-set annotation only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from enterprise_rag.services.quality_service import get_quality_service  # noqa: E402


DEV_ROOT = (PROJECT_ROOT / "eval" / "dev").resolve()
HOLDOUT_ROOT = (PROJECT_ROOT / "eval" / "holdout").resolve()


def _validate_output(path: Path) -> Path:
    output = path.resolve()
    try:
        output.relative_to(HOLDOUT_ROOT)
    except ValueError:
        pass
    else:
        raise ValueError("不能由反馈自动写入 eval/holdout")
    try:
        output.relative_to(DEV_ROOT)
    except ValueError as exc:
        raise ValueError(f"输出必须位于 {DEV_ROOT}") from exc
    return output


def export_cases(output: Path, *, limit: int = 200, overwrite: bool = False) -> int:
    target = _validate_output(output)
    if target.exists() and not overwrite:
        raise FileExistsError("输出已存在；使用 --overwrite 明确覆盖")
    service = get_quality_service()
    if service is None:
        raise RuntimeError("quality capture is disabled or QUALITY_ENCRYPTION_KEY is not configured")
    records = service.export_accepted_feedback(limit=limit)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for item in records:
        lines.append(json.dumps({
            "schema_version": 1,
            "origin": "quality_feedback",
            "review_status": "accepted",
            "manual_label_required": True,
            "feedback_id": item["id"],
            "message_id": item["message_id"],
            "verdict": item["verdict"],
            "reason": item["reason"],
            "adjudication_result": item["adjudication_result"],
            "query": item["query"],
            "answer": item["answer"],
            "sources": item["sources"],
            "terminal": item["terminal"],
            "error_code": item["error_code"],
            "created_at_ms": item["created_at_ms"],
            "reviewed_at_ms": item["reviewed_at_ms"],
        }, ensure_ascii=False))
    target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(records)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        count = export_cases(args.output, limit=args.limit, overwrite=args.overwrite)
    except (FileExistsError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"exported": count, "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
