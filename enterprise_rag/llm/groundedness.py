"""Non-destructive checks for source-reference syntax.

These helpers intentionally validate only whether a response refers to source
identifiers made available to that response. They do not claim semantic
entailment or factual correctness.
"""

from __future__ import annotations

import re
from typing import Iterable


_CITATION_GROUP = re.compile(r"\[([^\]]+)\]")
_SOURCE_ID = re.compile(r"^S([1-9]\d*)$")


def cited_source_ids(text: str) -> list[str]:
    """Extract ordered, unique `[S<n>]` references without guessing prose claims."""
    found: list[str] = []
    seen: set[str] = set()
    for group in _CITATION_GROUP.findall(text or ""):
        for token in group.split(","):
            source_id = token.strip().upper()
            if _SOURCE_ID.fullmatch(source_id) and source_id not in seen:
                seen.add(source_id)
                found.append(source_id)
    return found


def validate_citations(text: str, source_ids: Iterable[str]) -> dict[str, object]:
    """Return syntax/reference status only; never transform the answer text."""
    valid_ids = {str(item).strip().upper() for item in source_ids if str(item).strip()}
    cited = cited_source_ids(text)
    unknown = [source_id for source_id in cited if source_id not in valid_ids]
    return {
        "has_citations": bool(cited),
        "cited_ids": cited,
        "unknown_ids": unknown,
        "valid": not unknown,
    }
