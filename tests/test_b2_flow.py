from types import SimpleNamespace


def test_greeting_returns_static_reply_without_rag(monkeypatch):
    from enterprise_rag.services import chat_service

    service = chat_service.ChatService()
    service.rag_service = SimpleNamespace(
        answer_stream=lambda *_args: (_ for _ in ()).throw(AssertionError("RAG must not run"))
    )

    content = "".join(service.chat_stream("你好"))

    assert "企业知识库助手" in content
    assert service._last_meta["sources"] == []


def test_non_greeting_always_uses_rag_without_intent_routing(monkeypatch):
    from enterprise_rag.services import chat_service

    messages = [{"role": "user", "content": "写一封请假邮件"}]
    captured = {}
    monkeypatch.setattr(chat_service, "get_state", lambda *_args: messages)
    service = chat_service.ChatService()
    service.rag_service = SimpleNamespace(
        answer_stream=lambda query, history, current_messages: (
            captured.update(
                {"query": query, "history": history, "messages": current_messages}
            )
            or iter(["grounded"])
        )
    )

    assert "".join(service.chat_stream("写一封请假邮件")) == "grounded"
    assert captured == {
        "query": "写一封请假邮件",
        "history": "",
        "messages": messages,
    }


def test_retrieval_miss_rejects_without_calling_the_model(monkeypatch):
    from enterprise_rag.services import rag_service

    generated = []
    monkeypatch.setattr(
        rag_service,
        "retrieve_context",
        lambda *_args, **_kwargs: ("", [], []),
    )
    monkeypatch.setattr(
        rag_service,
        "generate_answer_stream",
        lambda *_args, **_kwargs: generated.append(True) or iter(["must not be used"]),
    )

    service = rag_service.RagService()
    content = "".join(service.answer_stream("不存在的制度", "", []))

    assert "未找到" in content
    assert generated == []
    assert service._last_meta["fallback_allowed"] is True
    assert service._last_meta["draft_allowed"] is False
    assert service._last_meta["is_reject"] is True


def test_general_fallback_is_explicit_and_never_persists_in_rag_cache(monkeypatch):
    from enterprise_rag.services import rag_service

    cache_calls = []
    prompts = []
    monkeypatch.setattr(
        rag_service,
        "generate_answer_stream",
        lambda system_prompt, *_args, **_kwargs: prompts.append(system_prompt) or iter(["通用回答"]),
    )
    service = rag_service.RagService()
    monkeypatch.setattr(service.cache_service, "set", lambda *_args: cache_calls.append(True))

    content = "".join(service.general_stream("写一封请假邮件"))

    assert content == "通用回答"
    assert cache_calls == []
    assert service._last_meta["is_general"] is True
    assert service._last_meta["sources"] == []
    assert "knowledge cutoff" in prompts[0]
