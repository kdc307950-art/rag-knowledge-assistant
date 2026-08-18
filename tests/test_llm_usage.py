"""Provider usage collection must be additive and stream-compatible."""

from __future__ import annotations

from types import SimpleNamespace


def _chunk(content=None, usage=None):
    return SimpleNamespace(
        choices=(
            [SimpleNamespace(delta=SimpleNamespace(content=content))]
            if content is not None
            else []
        ),
        usage=usage,
    )


def test_non_stream_usage_is_forwarded_to_metrics(monkeypatch):
    from enterprise_rag.llm import client

    calls = []
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
        choices=[],
    )

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return response

    monkeypatch.setattr(client, "get_llm", lambda: SimpleNamespace(chat=SimpleNamespace(completions=Completions())))
    monkeypatch.setattr(client, "_record_llm", lambda mode, duration, usage=None: calls.append((mode, usage)))

    assert client._call_llm([{"role": "user", "content": "hi"}]) is response
    assert calls[0]["stream"] is False
    assert calls[-1] == ("request", {"input": 11, "output": 7})


def test_stream_usage_is_collected_from_terminal_chunk(monkeypatch):
    from enterprise_rag.llm import client

    usage_calls = []
    response = [
        _chunk("A"),
        _chunk(usage=SimpleNamespace(prompt_tokens=13, completion_tokens=5)),
    ]
    monkeypatch.setattr(client, "_call_llm", lambda *args, **kwargs: response)
    monkeypatch.setattr(client, "_record_llm_tokens", lambda mode, usage: usage_calls.append((mode, usage)))

    assert "".join(client.generate_answer_stream("system", [])) == "A"
    assert usage_calls == [("stream", {"input": 13, "output": 5})]


def test_stream_usage_option_falls_back_once_before_first_chunk(monkeypatch):
    from enterprise_rag.llm import client

    # The runtime default is conservative for DeepSeek/OpenAI-compatible
    # endpoints; this test explicitly exercises the optional parameter path.
    monkeypatch.setattr(client, "LLM_STREAM_USAGE_MODE", "on")
    calls = []
    unsupported = ValueError("stream_options is not supported")
    unsupported.status_code = 400
    fallback_response = [
        _chunk("ok"),
        _chunk(usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1)),
    ]

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise unsupported
            return fallback_response

    monkeypatch.setattr(client, "get_llm", lambda: SimpleNamespace(chat=SimpleNamespace(completions=Completions())))
    token_calls = []
    monkeypatch.setattr(client, "_record_llm_tokens", lambda mode, usage: token_calls.append((mode, usage)))

    assert "".join(client.generate_answer_stream("system", [])) == "ok"
    assert calls[0]["stream_options"] == {"include_usage": True}
    assert "stream_options" not in calls[1]
    assert token_calls == [("stream", {"input": 2, "output": 1})]


def test_stream_fallback_does_not_retry_after_a_chunk(monkeypatch):
    from enterprise_rag.core.exceptions import LLMException
    from enterprise_rag.llm import client

    class LazyStream:
        def __iter__(self):
            yield _chunk("first")
            error = ValueError("stream_options is not supported")
            error.status_code = 400
            raise error

    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return LazyStream()

    monkeypatch.setattr(client, "get_llm", lambda: SimpleNamespace(chat=SimpleNamespace(completions=Completions())))

    try:
        list(client.generate_answer_stream("system", []))
    except LLMException:
        pass
    else:
        raise AssertionError("stream failure after first chunk must propagate")
    assert len(calls) == 1


def test_auto_mode_keeps_custom_openai_compatible_endpoint_unchanged(monkeypatch):
    from enterprise_rag.llm import client

    calls = []
    monkeypatch.setattr(client, "BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(client, "LLM_STREAM_USAGE_MODE", "auto")
    monkeypatch.setattr(client, "_record_llm", lambda *args, **kwargs: None)

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return []

    monkeypatch.setattr(
        client,
        "get_llm",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
    )

    client._call_llm([], stream=True)

    assert "stream_options" not in calls[0]


def test_llm_call_retains_retry_policy_after_usage_configuration():
    from enterprise_rag.llm import client

    assert hasattr(client._call_llm, "retry")
    assert not hasattr(client._stream_usage_enabled, "retry")


def test_stream_usage_accepts_mapping_chunks(monkeypatch):
    from enterprise_rag.llm import client

    response = [
        {"choices": [{"delta": {"content": "映射"}}]},
        {"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 2}},
    ]
    usage_calls = []
    monkeypatch.setattr(client, "_call_llm", lambda *args, **kwargs: response)
    monkeypatch.setattr(
        client,
        "_record_llm_tokens",
        lambda mode, usage: usage_calls.append((mode, usage)),
    )

    assert "".join(client.generate_answer_stream("system", [])) == "映射"
    assert usage_calls == [("stream", {"input": 4, "output": 2})]


def test_deepseek_usage_extracts_cache_hit_and_miss_tokens():
    from enterprise_rag.llm import client

    usage = client._extract_usage({
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_cache_hit_tokens": 30,
            "prompt_cache_miss_tokens": 70,
        }
    })

    assert usage == {
        "input": 100,
        "output": 20,
        "cached_input": 30,
        "uncached_input": 70,
    }


def test_auto_mode_enables_terminal_usage_for_deepseek(monkeypatch):
    from enterprise_rag.llm import client

    monkeypatch.setattr(client, "BASE_URL", "https://api.deepseek.com")
    monkeypatch.setattr(client, "LLM_STREAM_USAGE_MODE", "auto")

    assert client._stream_usage_enabled() is True


def test_stream_object_usage_is_not_recorded_before_terminal_chunk(monkeypatch):
    from enterprise_rag.llm import client

    calls = []
    response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=9, completion_tokens=3))

    class Completions:
        def create(self, **kwargs):
            return response

    monkeypatch.setattr(
        client,
        "get_llm",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
    )
    monkeypatch.setattr(
        client,
        "_record_llm",
        lambda mode, duration, usage=None: calls.append((mode, usage)),
    )

    assert client._call_llm([], stream=True) is response
    assert calls[-1] == ("stream", None)
