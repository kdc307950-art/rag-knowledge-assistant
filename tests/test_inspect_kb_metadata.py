"""The metadata inspector emits one row per parent evidence anchor."""

from __future__ import annotations


def test_inspector_deduplicates_child_chunks(monkeypatch, tmp_path):
    from scripts import inspect_kb_metadata

    class Collection:
        def get(self, **_kwargs):
            return {
                "metadatas": [
                    {"source": "book.txt", "revision_id": "r1", "parent_id": "p1", "chapter": "A", "paragraph": "1"},
                    {"source": "book.txt", "revision_id": "r1", "parent_id": "p1", "chapter": "A", "paragraph": "1"},
                    {"source": "book.txt", "revision_id": "r1", "parent_id": "p2", "chapter": "B", "paragraph": "2"},
                ],
                "documents": ["one", "one-child", "two"],
            }

    class Client:
        def __init__(self, path):
            assert str(tmp_path) == path

        def get_collection(self, name):
            assert name == "kb_text"
            return Collection()

    monkeypatch.setattr(inspect_kb_metadata.chromadb, "PersistentClient", Client)

    rows = inspect_kb_metadata.inspect(data_dir=tmp_path)

    assert [row["parent_id"] for row in rows] == ["p1", "p2"]
