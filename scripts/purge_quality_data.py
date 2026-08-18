"""Delete quality runs and feedback older than the configured retention period."""

from __future__ import annotations

import json
import sys

from enterprise_rag.services.quality_service import get_quality_service


def main() -> int:
    service = get_quality_service()
    if service is None:
        print("quality capture is disabled or QUALITY_ENCRYPTION_KEY is not configured", file=sys.stderr)
        return 2
    print(json.dumps(service.purge_expired(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
