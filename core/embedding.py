"""
Dense article embeddings (Phase 3 — the storage + generation half of Pillar A).

Emits a 768-dim vector per article into ``article_embeddings`` via the Model
Gateway's ``TaskType.EMBEDDING`` (Gemini ``gemini-embedding-001`` primary, no
fallback yet).  Both the ingest step (core/bg_refresher.py) and the manual
backfill (backfill_embeddings.py) drive the SAME ``embed_unembedded_articles``
loop, so there is exactly one code path for "embed the articles that don't have
an embedding yet".

Resumability is the database, not a cursor file: ``get_unembedded_articles``
returns only rows whose ``article_id`` has no ``article_embeddings`` row, so a
re-run simply picks up wherever the previous run stopped — the LEFT JOIN to
``article_embeddings`` is the checkpoint.  Everything here is best-effort:
embedding is additive and must never fail or stall an ingestion run.
"""
import asyncio
import logging
import time
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def compose_embed_text(article: Dict) -> str:
    """Build the exact string that gets embedded for an article.

    ``"title summary"`` — the repo-wide embeddable-text precedent (classifier
    keyword extraction and the FTS weights both use title + summary; there is
    no full-body field on ``articles``).  Must stay in sync with the
    ``chunk_text`` stored on each embedding row.
    """
    title = (article.get("title") or "").strip()
    summary = (article.get("summary") or "").strip()
    return f"{title} {summary}".strip()


def _gateway_ready() -> bool:
    """True when the gateway has at least one provider configured (else every
    embed call would fail with the fully-formed failure path — skip early)."""
    try:
        from core.ai_gateway.gateway import get_gateway

        return bool(get_gateway().providers)
    except Exception:
        return False


def embed_texts(
    texts: List[str],
    dimensions: int = 768,
    preferred_model: Optional[str] = None,
) -> Tuple[List[List[float]], Optional[str]]:
    """Embed a batch of texts through the gateway.

    Returns ``(vectors, model_name)``.  ``vectors`` is a list parallel to
    ``texts``; on any gateway failure (no provider, judge-style infra error) it
    returns ``([], None)`` — the caller treats that as a skipped batch and
    never writes partial or garbage vectors.
    """
    if not texts:
        return [], None
    from core.ai_gateway.contracts import AITaskRequest, TaskType
    from core.ai_gateway.gateway import get_gateway

    request = AITaskRequest(
        task=TaskType.EMBEDDING,
        input="",  # no prompt for embeddings — the text lives in embed_inputs
        embed_inputs=texts,
        embedding_dimensions=dimensions,
        allow_deterministic_fallback=False,
        preferred_model=preferred_model,
        metadata={"caller": "embedding"},
    )
    try:
        result = asyncio.run(get_gateway().execute(request))
    except Exception as e:
        logger.warning("Embedding call failed: %s", e)
        return [], None
    if not result.is_success():
        logger.warning(
            "Embedding call unsuccessful: %s",
            getattr(result.provenance, "error", "unknown"),
        )
        return [], None
    vectors = result.result if isinstance(result.result, list) else []
    model = getattr(result.provenance, "model", None)
    if len(vectors) != len(texts):
        logger.warning(
            "Embedding mismatch: %d texts in, %d vectors out — skipping batch",
            len(texts), len(vectors),
        )
        return [], None
    return vectors, model


def _preferred_embedding_model(model_key: Optional[str]) -> Optional[str]:
    """Pin embeddings to ``model_key`` only when it is a configured provider
    key, otherwise fall back to the gateway's default ``embedding`` route."""
    if not model_key:
        return None
    try:
        from core.ai_gateway.gateway import get_gateway

        return model_key if model_key in get_gateway().providers else None
    except Exception:
        return None


def embed_unembedded_articles(
    limit: int = 200,
    dry_run: bool = False,
) -> int:
    """Embed up to ``limit`` articles that currently lack an embedding.

    Returns the number of articles embedded (or that would have been, in dry
    run).  Never raises: unavailable Supabase, no gateway provider, disabled
    setting, and per-batch embedding failures all degrade to a skipped batch
    with a debug/warning log — embedding is advisory, not gating.
    """
    from config.settings import get_embedding_settings

    settings = get_embedding_settings()
    if not settings.get("embedding_enabled", True):
        return 0
    if not _gateway_ready():
        return 0

    try:
        from core.supabase_client import get_supabase_manager

        manager = get_supabase_manager()
        if not manager or not manager.is_available():
            return 0
    except Exception as e:
        logger.debug("Embedding skipped (no Supabase manager): %s", e)
        return 0

    articles = manager.get_unembedded_articles(limit=limit)
    if not articles:
        return 0

    batch_size = int(settings.get("embedding_batch_size", 50))
    dimensions = int(settings.get("embedding_dimensions", 768))
    preferred = _preferred_embedding_model(settings.get("embedding_model"))

    embedded = 0
    for i in range(0, len(articles), batch_size):
        batch = articles[i:i + batch_size]
        texts = [compose_embed_text(a) for a in batch]
        vectors, model = embed_texts(texts, dimensions=dimensions, preferred_model=preferred)
        if not vectors:
            continue

        rows: List[Dict] = []
        for article, text, vector in zip(batch, texts, vectors):
            run_date = article.get("run_date") or ""
            if not run_date:
                # Fall back to today — run_date is denormalized for Phase 5
                # filtering, and a missing value must not block the insert.
                run_date = time.strftime("%Y-%m-%d")
            rows.append({
                "article_id": article.get("article_id"),
                "run_date": run_date,
                "chunk_text": text,
                "embedding": vector,
                "embedding_model": model or settings.get("embedding_model") or "unknown",
            })

        if dry_run:
            embedded += len(rows)
            continue
        if manager.insert_article_embeddings(rows):
            embedded += len(rows)

    logger.info("Embedded %d article(s) this pass", embedded)
    return embedded


def reembed_articles(
    total_limit: int = 0,
    batch_size: Optional[int] = None,
    dry_run: bool = False,
    between_batches_sleep: Optional[float] = None,
) -> int:
    """Re-embed **existing** ``article_embeddings`` rows with the routed provider.

    This is the migration-window companion to ``backfill_embeddings``:
    ``get_unembedded_articles()`` only returns rows *without* a vector, so the
    old-model rows would otherwise never be revisited and the index could not
    reach model uniformity.  It reads the stored ``chunk_text`` (the exact
    string originally embedded) and writes through the same
    ``insert_article_embeddings()`` upsert path, replacing each row in place.

    Run only after flipping ``EMBEDDING_PROVIDER`` — and drain the normal
    backlog afterwards.  ``total_limit`` 0 means "every embedded row".
    Never raises: the usual best-effort contract applies (a failed batch stops
    the loop and is retried on the next run).
    """
    from config.settings import (
        get_embedding_settings,
        EMBEDDING_PROVIDER,
        EMBEDDING_ROUTE_MODEL,
        GEMINI_EMBEDDING_MODEL,
    )

    settings = get_embedding_settings()
    if not settings.get("embedding_enabled", True):
        return 0
    if not _gateway_ready():
        return 0

    try:
        from core.supabase_client import get_supabase_manager

        manager = get_supabase_manager()
        if not manager or not manager.is_available():
            return 0
    except Exception as e:
        logger.debug("Re-embed skipped (no Supabase manager): %s", e)
        return 0

    batch = batch_size or int(settings.get("embedding_batch_size", 50))
    dimensions = int(settings.get("embedding_dimensions", 768))
    sleep = (
        between_batches_sleep
        if between_batches_sleep is not None
        else float(settings.get("embedding_backfill_sleep", 0.5))
    )
    preferred = _preferred_embedding_model(settings.get("embedding_model"))
    target_model = EMBEDDING_ROUTE_MODEL.get(
        EMBEDDING_PROVIDER, GEMINI_EMBEDDING_MODEL
    )

    total = 0
    while True:
        remaining = (total_limit - total) if total_limit else batch
        if remaining <= 0:
            break
        page_size = min(batch, remaining)
        # Offset stays 0: each successful upsert removes its rows from the
        # ``exclude_model`` filter, so the next query naturally returns the
        # next page.  This makes a rerun resume after an interruption instead
        # of re-embedding the same first rows.
        rows = manager.get_articles_for_reembedding(
            limit=page_size, offset=0, exclude_model=target_model
        )
        if not rows:
            break

        pairs = [
            (row, (row.get("chunk_text") or "").strip())
            for row in rows
        ]
        pairs = [(row, text) for row, text in pairs if text]
        if not pairs:
            # Nothing embeddable in this page; stop rather than spin.
            break

        texts = [text for _, text in pairs]
        vectors, model = embed_texts(
            texts, dimensions=dimensions, preferred_model=preferred
        )
        if not vectors:
            # Stop rather than spin: the next run resumes with the remaining
            # old-model rows.
            break

        out: List[Dict] = []
        for (row, text), vector in zip(pairs, vectors):
            out.append({
                "article_id": row.get("article_id"),
                "run_date": row.get("run_date") or time.strftime("%Y-%m-%d"),
                "chunk_text": text,
                "embedding": vector,
                "embedding_model": model or settings.get("embedding_model") or "unknown",
            })

        if dry_run:
            total += len(out)
        elif manager.insert_article_embeddings(out):
            total += len(out)
        else:
            break

        if total_limit and total >= total_limit:
            break
        if sleep > 0:
            time.sleep(sleep)

    logger.info("Re-embedded %d existing article(s) this pass", total)
    return total


def backfill_embeddings(
    total_limit: int = 0,
    batch_size: Optional[int] = None,
    dry_run: bool = False,
    between_batches_sleep: Optional[float] = None,
) -> int:
    """Drain the unembedded backlog until empty (or ``total_limit`` reached).

    ``total_limit`` 0 means "until drained".  Each pass embeds at most one
    batch then re-queries, so the loop terminates the moment the unembedded set
    is empty — and re-running always resumes exactly where it stopped.  Sleeps
    ``between_batches_sleep`` seconds between passes for rate-limit headroom.
    """
    from config.settings import get_embedding_settings

    settings = get_embedding_settings()
    batch = batch_size or int(settings.get("embedding_batch_size", 50))
    sleep = (
        between_batches_sleep
        if between_batches_sleep is not None
        else float(settings.get("embedding_backfill_sleep", 0.5))
    )

    total = 0
    while True:
        remaining = (total_limit - total) if total_limit else batch
        if remaining <= 0:
            break
        n = embed_unembedded_articles(limit=min(remaining, batch), dry_run=dry_run)
        total += n
        if n == 0:
            break
        if total_limit and total >= total_limit:
            break
        if sleep > 0:
            time.sleep(sleep)
    return total