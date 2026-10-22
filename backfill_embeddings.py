"""Manual backfill of dense article embeddings (Phase 3, roadmap 3.4).

Drains the ``article_embeddings`` backlog by running the same
``embed_unembedded_articles`` loop the ingest step uses, so there is one code
path and both stay in sync.  Resumability is the database, not a cursor file:
``get_unembedded_articles`` returns only articles without an embedding row, so
re-running simply resumes wherever the previous run stopped.

Usage:
    python backfill_embeddings.py                  # drain the whole backlog
    python backfill_embeddings.py --limit 20       # embed at most 20 articles
    python backfill_embeddings.py --dry-run        # count without writing
    python backfill_embeddings.py --sleep 2 --batch-size 100

Requires GEMINI_API_KEY (and the Phase 3 migration applied) to write anything;
with no key or no migration the run reports 0 embedded and exits cleanly.
"""
import argparse
import time

from core.logger import setup_logger

logger = setup_logger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill dense article embeddings into Supabase (Phase 3)."
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="max articles to embed (0 = drain the whole backlog)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=None,
        help="texts per embedding call (default from get_embedding_settings())",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="count embeddable articles without writing anything",
    )
    parser.add_argument(
        "--sleep", type=float, default=None,
        help="seconds between batches (default from get_embedding_settings())",
    )
    args = parser.parse_args()

    from core.embedding import backfill_embeddings

    started = time.time()
    total = backfill_embeddings(
        total_limit=args.limit,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        between_batches_sleep=args.sleep,
    )
    elapsed = time.time() - started
    verb = "would embed" if args.dry_run else "embedded"
    print(f"Backfill complete: {verb} {total} article(s) in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())