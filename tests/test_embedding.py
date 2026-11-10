"""
Tests for the Phase 3 embedding pipeline: gateway dispatch, SupabaseManager
methods, and the embed_unembedded_articles orchestration loop.
"""

import asyncio
from unittest.mock import MagicMock, patch

from core.ai_gateway.contracts import AITaskRequest, TaskType
from core.ai_gateway.gateway import ModelGateway
from core.supabase_client import SupabaseManager


# ---------------------------------------------------------------------------
# Contracts / gateway dispatch
# ---------------------------------------------------------------------------

def test_embedding_tasktype_and_request_estimates_batch_tokens():
    req = AITaskRequest(task=TaskType.EMBEDDING, input="", embed_inputs=["ab", "cd"])
    assert TaskType.EMBEDDING.value == "embedding"
    # Batch estimate uses embed_inputs, not the empty prompt string.
    assert req.estimated_input_tokens() == 4 // 3


class _FakeEmbedProvider:
    async def embed(self, texts, **kwargs):
        return {
            "vectors": [[1.0] * kwargs.get("dimensions", 768) for _ in texts],
            "usage": {"input_tokens": 7},
            "model": "gemini-embedding-001",
        }


def test_execute_with_validation_embedding_branch():
    """EMBEDDING skips prompt/schema and returns (vectors, usage)."""
    gateway = ModelGateway({"model_registry": {}, "routing_policies": {}})
    provider = _FakeEmbedProvider()
    req = AITaskRequest(
        task=TaskType.EMBEDDING, input="", embed_inputs=["a", "b"], embedding_dimensions=768
    )
    data, usage = asyncio.run(gateway._execute_with_validation(provider, req, "key"))
    assert data == [[1.0] * 768, [1.0] * 768]
    assert usage == {"input_tokens": 7}


# ---------------------------------------------------------------------------
# SupabaseManager methods
# ---------------------------------------------------------------------------

def _rpc_chain(rows):
    r = MagicMock()
    r.execute.return_value = MagicMock(data=rows)
    return r


def test_get_unembedded_articles_uses_rpc_and_normalises_run_date():
    manager = SupabaseManager()
    manager.available = True
    rpc = _rpc_chain([
        {"article_id": "a1", "run_date": "2026-08-01", "title": "T", "summary": "S", "content_hash": "h1"},
    ])
    manager.client = MagicMock()
    manager.client.rpc.return_value = rpc

    rows = manager.get_unembedded_articles(limit=50)

    assert rows[0]["article_id"] == "a1"
    assert rows[0]["run_date"] == "2026-08-01"
    manager.client.rpc.assert_called_with("get_unembedded_articles", {"limit_count": 50})


def test_get_unembedded_articles_empty_when_rpc_missing():
    manager = SupabaseManager()
    manager.available = True
    manager.client = MagicMock()
    manager.client.rpc.side_effect = Exception("function not found")
    assert manager.get_unembedded_articles() == []


def test_get_unembedded_articles_unavailable():
    manager = SupabaseManager()
    manager.available = False
    assert manager.get_unembedded_articles() == []


def test_insert_article_embeddings_upserts_on_article_id():
    manager = SupabaseManager()
    manager.available = True
    manager.client = MagicMock()
    table = manager.client.table.return_value
    rows = [{"article_id": "a1"}]

    assert manager.insert_article_embeddings(rows) is True

    manager.client.table.assert_called_with("article_embeddings")
    # Re-embedding after a provider migration must overwrite, not append
    # (article_embeddings has UNIQUE(article_id)).
    table.upsert.assert_called_once_with(rows, on_conflict="article_id")
    table.upsert.return_value.execute.assert_called_once()


def test_insert_article_embeddings_unavailable():
    manager2 = SupabaseManager()
    manager2.available = False
    assert manager2.insert_article_embeddings([{"article_id": "a1"}]) is False


def test_match_article_embeddings_returns_rows_and_none_when_unavailable():
    manager = SupabaseManager()
    manager.available = True
    rpc = _rpc_chain([{"article_id": "a1", "similarity": 0.9}])
    manager.client = MagicMock()
    manager.client.rpc.return_value = rpc
    result = manager.match_article_embeddings([0.1] * 768, match_count=5)
    assert result == [{"article_id": "a1", "similarity": 0.9}]
    manager.client.rpc.assert_called_with(
        "match_article_embeddings", {"query_embedding": [0.1] * 768, "match_count": 5}
    )

    manager2 = SupabaseManager()
    manager2.available = False
    assert manager2.match_article_embeddings([0.1] * 768) is None


# ---------------------------------------------------------------------------
# Orchestration loop (core/embedding.py)
# ---------------------------------------------------------------------------

def test_compose_embed_text():
    from core.embedding import compose_embed_text

    assert compose_embed_text({"title": "Title", "summary": "Summary"}) == "Title Summary"
    assert compose_embed_text({"title": "Title", "summary": None}) == "Title"
    assert compose_embed_text({"title": None, "summary": "Summary"}) == "Summary"
    assert compose_embed_text({}) == ""


def test_embed_unembedded_articles_returns_zero_without_gateway(monkeypatch):
    """No gateway provider → short-circuit to 0 (no Supabase touched)."""
    from core import embedding as emb

    monkeypatch.setattr(emb, "_gateway_ready", lambda: False)
    assert emb.embed_unembedded_articles() == 0


def test_embed_unembedded_articles_returns_zero_offline(monkeypatch):
    """Offline Supabase (autouse stub) → 0, even with a gateway provider."""
    from core import embedding as emb

    monkeypatch.setattr(emb, "_gateway_ready", lambda: True)
    # autouse conftest fixture pins core.supabase_client._manager offline
    assert emb.embed_unembedded_articles() == 0


def test_embed_unembedded_articles_writes_rows(monkeypatch):
    from core import embedding as emb

    manager = MagicMock()
    manager.is_available.return_value = True
    manager.get_unembedded_articles.return_value = [
        {"article_id": "a1", "run_date": "2026-08-01", "title": "T1", "summary": "S1"},
        {"article_id": "a2", "run_date": "2026-08-02", "title": "T2", "summary": "S2"},
    ]
    manager.insert_article_embeddings.return_value = True

    vectors = [[0.0] * 768, [0.1] * 768]
    monkeypatch.setattr(emb, "_gateway_ready", lambda: True)
    monkeypatch.setattr(
        emb, "embed_texts", lambda texts, **kw: (vectors, "gemini-embedding-001")
    )
    monkeypatch.setattr("core.supabase_client.get_supabase_manager", lambda: manager)

    count = emb.embed_unembedded_articles(limit=10)

    assert count == 2
    inserted = manager.insert_article_embeddings.call_args[0][0]
    assert [r["article_id"] for r in inserted] == ["a1", "a2"]
    assert [r["chunk_text"] for r in inserted] == ["T1 S1", "T2 S2"]
    assert all(r["embedding_model"] == "gemini-embedding-001" for r in inserted)
    assert all(len(r["embedding"]) == 768 for r in inserted)


def test_get_articles_for_reembedding_paginates_by_article_id():
    manager = SupabaseManager()
    manager.available = True
    manager.client = MagicMock()
    table = manager.client.table.return_value
    chain = table.select.return_value.order.return_value.range.return_value
    chain.execute.return_value = MagicMock(
        data=[
            {"article_id": "a1", "run_date": "2026-08-01", "chunk_text": "T S"},
            {"article_id": None, "run_date": "2026-08-02", "chunk_text": "skip"},
        ]
    )

    rows = manager.get_articles_for_reembedding(limit=10, offset=20)

    assert rows == [
        {"article_id": "a1", "run_date": "2026-08-01", "chunk_text": "T S"}
    ]
    manager.client.table.assert_called_with("article_embeddings")
    table.select.assert_called_with("article_id, run_date, chunk_text")
    table.select.return_value.order.assert_called_with("article_id")
    table.select.return_value.order.return_value.range.assert_called_with(20, 29)


def test_get_articles_for_reembedding_excludes_target_model():
    manager = SupabaseManager()
    manager.available = True
    manager.client = MagicMock()
    table = manager.client.table.return_value
    query = table.select.return_value.neq.return_value
    query.order.return_value.range.return_value.execute.return_value = MagicMock(data=[])

    manager.get_articles_for_reembedding(
        limit=10, exclude_model="@cf/baai/bge-base-en-v1.5"
    )

    table.select.return_value.neq.assert_called_once_with(
        "embedding_model", "@cf/baai/bge-base-en-v1.5"
    )


def test_get_articles_for_reembedding_unavailable():
    manager = SupabaseManager()
    manager.available = False
    assert manager.get_articles_for_reembedding() == []


def test_reembed_articles_overwrites_existing_rows_with_routed_model(monkeypatch):
    from core import embedding as emb
    import config.settings as settings_mod

    monkeypatch.setattr(settings_mod, "EMBEDDING_PROVIDER", "cloudflare")
    monkeypatch.setattr(
        settings_mod,
        "EMBEDDING_ROUTE_MODEL",
        {
            "gemini": "gemini-embedding-001",
            "cloudflare": "@cf/baai/bge-base-en-v1.5",
        },
    )

    manager = MagicMock()
    manager.is_available.return_value = True
    manager.get_articles_for_reembedding.side_effect = [
        [
            {"article_id": "a1", "run_date": "2026-08-01", "chunk_text": "T1 S1"},
            {"article_id": "a2", "run_date": "2026-08-02", "chunk_text": ""},
        ],
        [],
    ]
    manager.insert_article_embeddings.return_value = True

    monkeypatch.setattr(emb, "_gateway_ready", lambda: True)
    monkeypatch.setattr(
        emb,
        "embed_texts",
        lambda texts, **kw: ([[0.2] * 768], "@cf/baai/bge-base-en-v1.5"),
    )
    monkeypatch.setattr("core.supabase_client.get_supabase_manager", lambda: manager)

    count = emb.reembed_articles(batch_size=2, between_batches_sleep=0)

    assert count == 1  # empty chunk_text is not re-embedded
    manager.get_articles_for_reembedding.assert_called_with(
        limit=2, offset=0, exclude_model="@cf/baai/bge-base-en-v1.5"
    )
    inserted = manager.insert_article_embeddings.call_args[0][0]
    assert len(inserted) == 1
    assert inserted[0]["article_id"] == "a1"
    assert inserted[0]["chunk_text"] == "T1 S1"
    assert inserted[0]["embedding_model"] == "@cf/baai/bge-base-en-v1.5"


def test_reembed_articles_stops_when_gateway_batch_fails(monkeypatch):
    from core import embedding as emb

    manager = MagicMock()
    manager.is_available.return_value = True
    manager.get_articles_for_reembedding.return_value = [
        {"article_id": "a1", "run_date": "2026-08-01", "chunk_text": "T1 S1"},
    ]

    monkeypatch.setattr(emb, "_gateway_ready", lambda: True)
    monkeypatch.setattr(emb, "embed_texts", lambda texts, **kw: ([], None))
    monkeypatch.setattr("core.supabase_client.get_supabase_manager", lambda: manager)

    assert emb.reembed_articles(batch_size=50, between_batches_sleep=0) == 0
    manager.insert_article_embeddings.assert_not_called()


def test_embed_unembedded_articles_skips_failed_batch(monkeypatch):
    from core import embedding as emb

    manager = MagicMock()
    manager.is_available.return_value = True
    manager.get_unembedded_articles.return_value = [
        {"article_id": "a1", "run_date": "2026-08-01", "title": "T", "summary": "S"},
    ]
    monkeypatch.setattr(emb, "_gateway_ready", lambda: True)
    monkeypatch.setattr(emb, "embed_texts", lambda texts, **kw: ([], None))
    monkeypatch.setattr("core.supabase_client.get_supabase_manager", lambda: manager)

    assert emb.embed_unembedded_articles(limit=10) == 0
    manager.insert_article_embeddings.assert_not_called()