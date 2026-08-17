"""测试运行数据隔离：禁止缓存、manifest 和脏标记写入真实部署目录。"""

import pytest


@pytest.fixture(autouse=True)
def isolate_runtime_metadata(monkeypatch, tmp_path):
    from enterprise_rag.storage import cache as cache_module
    from enterprise_rag.storage import kb_manifest

    monkeypatch.setattr(
        cache_module,
        "DEFAULT_CACHE_PATH",
        tmp_path / "answer_cache.sqlite3",
    )
    monkeypatch.setattr(
        kb_manifest,
        "DEFAULT_MANIFEST_PATH",
        tmp_path / "kb_manifest.sqlite3",
    )
