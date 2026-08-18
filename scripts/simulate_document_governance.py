"""Run an isolated HR/legal document-governance approval simulation.

This command never edits the production governance file, Chroma, or manifest.
It creates a candidate config under ``--workdir`` and validates it with the
same loader used by the application.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from enterprise_rag.storage.document_governance import load_governance  # noqa: E402
from enterprise_rag.storage.document_governance_workflow import (  # noqa: E402
    approve,
    build_candidate_config,
    create_application,
    write_simulation_artifacts,
)


def run_simulation(
    *,
    base_config_path: Path,
    workdir: Path,
    authoritative_source: str,
    superseded_sources: list[str],
) -> dict:
    base = load_governance(base_config_path)
    target = base["documents"].get(authoritative_source) or {}
    application = create_application(
        application_id="SIM-HR-LEGAL-001",
        document_family=str(target.get("document_family") or "employee_handbook"),
        authoritative_source=authoritative_source,
        superseded_sources=superseded_sources,
        version="SIM-2025.1",
        effective_from="2025-01-01",
        scope={"legal_entities": ["simulation-company"], "regions": ["all"], "employee_types": ["all"]},
        conflict_priority="simulation candidate document wins only after explicit approval",
    )
    approve(
        application,
        role="hr",
        actor="simulated-hr-owner",
        evidence_refs=["simulation/HR-approval-record.pdf", "simulation/notice-record.pdf"],
    )
    approve(
        application,
        role="legal",
        actor="simulated-legal-reviewer",
        evidence_refs=["simulation/legal-review-record.pdf"],
    )
    candidate = build_candidate_config(base, application)
    # Validate with the production parser before writing any artifact.
    candidate_path = workdir / "_candidate-validation.json"
    workdir.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
    load_governance(candidate_path)
    candidate_path.unlink()
    paths = write_simulation_artifacts(workdir, application, candidate)
    return {"stage": application["stage"], "production_config_unchanged": True, **paths}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-config", type=Path, default=PROJECT_ROOT / "config" / "document_governance.json")
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--authoritative-source", required=True)
    parser.add_argument("--superseded-source", action="append", required=True)
    args = parser.parse_args()
    result = run_simulation(
        base_config_path=args.base_config,
        workdir=args.workdir,
        authoritative_source=args.authoritative_source,
        superseded_sources=args.superseded_source,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
