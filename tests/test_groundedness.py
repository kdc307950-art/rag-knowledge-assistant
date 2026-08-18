"""Citation ID checks are intentionally syntactic and non-destructive."""

from __future__ import annotations

from types import SimpleNamespace


def test_validate_citations_accepts_only_known_source_ids():
    from enterprise_rag.llm.groundedness import validate_citations

    report = validate_citations("制度规定为五天。[S1, S2] 另见[S3]", ["S1", "S2"])

    assert report == {
        "has_citations": True,
        "cited_ids": ["S1", "S2", "S3"],
        "unknown_ids": ["S3"],
        "valid": False,
    }


def test_validate_citations_does_not_treat_non_source_brackets_as_evidence():
    from enterprise_rag.llm.groundedness import validate_citations

    report = validate_citations("[注意] 这不是引用；[s1] 是。", ["S1"])

    assert report == {
        "has_citations": True,
        "cited_ids": ["S1"],
        "unknown_ids": [],
        "valid": True,
    }


def test_retriever_assigns_per_answer_source_ids(monkeypatch):
    from enterprise_rag.rag import retriever

    monkeypatch.setattr(
        retriever,
        "get_search_function",
        lambda: lambda _query, n_results: {
            "documents": [["first", "second"]],
            "metadatas": [[
                {"source": "a.txt", "chapter": "A"},
                {"source": "b.txt", "chapter": "B"},
            ]],
            "distances": [[0.1, 0.2]],
        },
    )
    monkeypatch.setattr(
        retriever,
        "get_reranker",
        lambda: SimpleNamespace(predict=lambda _pairs: [0.9, 0.8]),
    )

    context, _sources, raw = retriever.retrieve_context("question", return_raw=True)

    assert "[S1 | 来源：a.txt" in context
    assert "[S2 | 来源：b.txt" in context
    assert [item["citation_id"] for item in raw] == ["S1", "S2"]
