from __future__ import annotations

import importlib

import pytest


MODEL_CASES = (
    ("enterprise_rag.storage.embedding", "MODEL_NAME", "get_model_readiness"),
    ("enterprise_rag.rag.reranker", "RERANKER_MODEL", "get_reranker_readiness"),
)


@pytest.fixture(params=MODEL_CASES, ids=("embedding", "reranker"))
def readiness_case(request, monkeypatch):
    module_name, model_attribute, readiness_function = request.param
    module = importlib.import_module(module_name)
    if hasattr(module, "_model"):
        monkeypatch.setattr(module, "_model", None)
    return module, model_attribute, getattr(module, readiness_function)


def test_model_readiness_online_allows_first_download(monkeypatch, readiness_case):
    module, model_attribute, get_readiness = readiness_case
    monkeypatch.setattr(module, model_attribute, "organization/model")
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")

    ready, detail = get_readiness()

    assert ready is True
    assert "首次使用时下载" in detail


def test_model_readiness_accepts_offline_hub_cache(monkeypatch, readiness_case):
    import huggingface_hub

    module, model_attribute, get_readiness = readiness_case
    monkeypatch.setattr(module, model_attribute, "organization/model")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(
        huggingface_hub,
        "try_to_load_from_cache",
        lambda *_args, **_kwargs: "cache/snapshots/revision/config.json",
    )

    ready, detail = get_readiness()

    assert ready is True
    assert "本地缓存可用" in detail


def test_model_readiness_rejects_offline_cache_miss(monkeypatch, readiness_case):
    import huggingface_hub

    module, model_attribute, get_readiness = readiness_case
    monkeypatch.setattr(module, model_attribute, "organization/model")
    monkeypatch.setenv("HF_HUB_OFFLINE", "true")
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda *_args, **_kwargs: None)

    ready, detail = get_readiness()

    assert ready is False
    assert "离线模式缺少" in detail


def test_model_readiness_rejects_cached_no_exist_sentinel(monkeypatch, readiness_case):
    import huggingface_hub

    module, model_attribute, get_readiness = readiness_case
    monkeypatch.setattr(module, model_attribute, "organization/model")
    monkeypatch.setenv("HF_HUB_OFFLINE", "yes")
    monkeypatch.setattr(
        huggingface_hub,
        "try_to_load_from_cache",
        lambda *_args, **_kwargs: huggingface_hub._CACHED_NO_EXIST,
    )

    ready, detail = get_readiness()

    assert ready is False
    assert "离线模式缺少" in detail


def test_model_readiness_accepts_configured_local_directory(
    monkeypatch, tmp_path, readiness_case
):
    import huggingface_hub

    module, model_attribute, get_readiness = readiness_case
    model_dir = tmp_path / "local-model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module, model_attribute, str(model_dir))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")

    def unexpected_cache_lookup(*_args, **_kwargs):
        raise AssertionError("本地模型目录不应查询 Hugging Face 缓存")

    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", unexpected_cache_lookup)

    ready, detail = get_readiness()

    assert ready is True
    assert "本地目录可用" in detail


def test_model_readiness_accepts_pathlike_local_directory(
    monkeypatch, tmp_path, readiness_case
):
    module, model_attribute, get_readiness = readiness_case
    model_dir = tmp_path / "pathlike-model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module, model_attribute, model_dir)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")

    ready, detail = get_readiness()

    assert ready is True
    assert "本地目录可用" in detail


def test_model_readiness_rejects_empty_local_directory(
    monkeypatch, tmp_path, readiness_case
):
    module, model_attribute, get_readiness = readiness_case
    model_dir = tmp_path / "empty-model"
    model_dir.mkdir()
    monkeypatch.setattr(module, model_attribute, model_dir)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")

    ready, detail = get_readiness()

    assert ready is False
    assert "离线模式缺少" in detail


def test_model_source_prefers_cached_snapshot(monkeypatch, tmp_path):
    """本地 Hub 缓存存在时，加载器应直接使用快照并禁止联网。"""
    import huggingface_hub
    from enterprise_rag.utils.model_readiness import resolve_hf_model_source

    snapshot = tmp_path / "snapshots" / "revision"
    snapshot.mkdir(parents=True)
    config = snapshot / "config.json"
    config.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        huggingface_hub,
        "try_to_load_from_cache",
        lambda *_args, **_kwargs: str(config),
    )
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")

    source, local_only = resolve_hf_model_source("organization/model")

    assert source == str(snapshot)
    assert local_only is True


def test_model_source_allows_download_when_cache_is_missing(monkeypatch):
    """在线模式且没有缓存时，应保留模型标识以允许首次下载。"""
    import huggingface_hub
    from enterprise_rag.utils.model_readiness import resolve_hf_model_source

    monkeypatch.setattr(
        huggingface_hub, "try_to_load_from_cache", lambda *_args, **_kwargs: None
    )
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")

    source, local_only = resolve_hf_model_source("organization/model")

    assert source == "organization/model"
    assert local_only is False
