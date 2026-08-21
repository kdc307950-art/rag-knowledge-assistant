"""Static deployment contracts that complement application unit tests."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_backend_image_includes_required_operations_scripts():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY scripts/ ./scripts/" in dockerfile
    assert 'PATH="/app/.venv/bin:$PATH"' in dockerfile
    assert 'CMD ["uvicorn", "backend.main:app"' in dockerfile
    for script in ("create_user.py", "backup.py", "health_check.py"):
        assert f"test -f /app/scripts/{script}" in dockerfile


def test_linux_production_uses_cpu_only_pytorch_index():
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"torch==2.13.0"' in project
    assert 'index = "pytorch-cpu", marker = "sys_platform == \'linux\'"' in project
    assert 'url = "https://download.pytorch.org/whl/cpu"' in project


def test_retired_streamlit_dependencies_are_not_direct_requirements():
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    retired = {
        "bleach",
        "markdown",
        "opencv-python-headless",
        "pandas",
        "pyarrow",
        "scipy",
    }
    direct_requirements = {
        match.group(1).lower()
        for match in re.finditer(r'^\s*"([A-Za-z0-9_.-]+)(?:\[[^]]+\])?(?:[<>=!~].*)?",$', project, re.MULTILINE)
    }
    assert retired.isdisjoint(direct_requirements)


def test_public_nginx_hides_internal_probes_and_leaves_upload_envelope():
    nginx = (ROOT / "nginx.conf").read_text(encoding="utf-8")
    assert re.search(r"location\s*=\s*/api/live\s*{\s*return\s+404;", nginx)
    assert re.search(r"location\s*=\s*/api/ready\s*{\s*return\s+404;", nginx)
    match = re.search(r"client_max_body_size\s+(\d+)m;", nginx)
    assert match is not None
    assert int(match.group(1)) > 200


def test_acme_webroot_is_shared_read_only_with_nginx():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    nginx = (ROOT / "nginx.conf").read_text(encoding="utf-8")
    assert "./certbot/www:/var/www/certbot:ro" in compose
    assert "root /var/www/certbot;" in nginx
