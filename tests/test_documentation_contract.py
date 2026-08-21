"""Contracts that keep controlled documentation aligned with implementation."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROLLED_DOCS = [
    ROOT / "README.md",
    ROOT / "backend" / "API.md",
    ROOT / "eval" / "README.md",
    ROOT / "frontend" / "README.md",
    *(ROOT / "docs").glob("*.md"),
]


def _application_version() -> str:
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', project, re.MULTILINE)
    assert match is not None
    return match.group(1)


def test_controlled_docs_declare_current_application_version():
    expected = f"`{_application_version()}`"
    for path in CONTROLLED_DOCS:
        header = "\n".join(path.read_text(encoding="utf-8").splitlines()[:10])
        assert expected in header, f"{path.relative_to(ROOT)} lacks {expected} in its header"


def test_all_relative_markdown_links_resolve():
    link_pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
    for path in [*CONTROLLED_DOCS, ROOT / "CHANGELOG.md"]:
        content = path.read_text(encoding="utf-8")
        for target in link_pattern.findall(content):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            relative_target = target.split("#", 1)[0]
            if not relative_target:
                continue
            resolved = path.parent / relative_target
            assert resolved.exists(), f"broken link in {path.relative_to(ROOT)}: {target}"


def test_api_reference_lists_every_runtime_openapi_path():
    from backend.main import app

    api_reference = (ROOT / "backend" / "API.md").read_text(encoding="utf-8")
    for route in app.openapi()["paths"]:
        assert f"`{route}`" in api_reference, f"API reference is missing {route}"


def test_documented_default_llm_matches_runtime_source():
    config = (ROOT / "enterprise_rag" / "config.py").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    base_url = re.search(r'BASE_URL\s*=\s*os\.getenv\("OPENAI_BASE_URL",\s*"([^"]+)"\)', config)
    model = re.search(r'LLM_MODEL\s*=\s*os\.getenv\("OPENAI_MODEL",\s*"([^"]+)"\)', config)
    assert base_url is not None and model is not None
    for value in (base_url.group(1), model.group(1)):
        assert value in env_example
        assert value in readme


def test_windows_time_of_day_pricing_declares_tzdata_dependency():
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'"tzdata[^\"]*sys_platform\s*==\s*\'win32\'[^\"]*"', project)
