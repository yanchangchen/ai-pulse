"""Manual backfill of Tier-3 monthly syntheses (Phase 6.1).

Runs the same ``synthesize_due_monthly`` runner the BackgroundRefresher
pipeline hook uses, so there is one code path and both stay in sync.
Resumability is the database, not a cursor file: the due-check reads
``monthly_syntheses`` and only synthesizes (month, theme) pairs that are
missing or stale, so re-running simply resumes wherever the previous run
stopped. This is also the entry point a future scheduled pipeline
(GitHub Actions cron) can call.

Usage:
    python backfill_monthly_syntheses.py                     # synthesize all due pairs
    python backfill_monthly_syntheses.py --dry-run           # list due pairs, no LLM/writes
    python backfill_monthly_syntheses.py --month 2026-06     # restrict to one month
    python backfill_monthly_syntheses.py --month 2026-06 --month 2026-07
    python backfill_monthly_syntheses.py --month 2026-06 --force   # re-synthesize even if fresh
    python backfill_monthly_syntheses.py --limit 7           # cap the number of calls

Requires Supabase credentials and the Phase 6.1 migration applied
(supabase_migration_monthly_syntheses.sql); offline or pre-migration the
run reports 0 and exits cleanly. LLM calls route through the Model Gateway
(TaskType.SYNTHESISE) with the usual provenance and llm_calls accounting.
"""
import argparse
import time

from core.logger import setup_logger

logger = setup_logger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill Tier-3 monthly syntheses into Supabase (Phase 6.1)."
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="max (month, theme) syntheses to write (0 = all due pairs)",
    )
    parser.add_argument(
        "--month", action="append", default=None, metavar="YYYY-MM",
        help="restrict to this month (repeatable)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="re-synthesize regardless of staleness (scoped to --month when given)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="list due pairs without calling the LLM or writing anything",
    )
    args = parser.parse_args()

    from core.monthly_synthesis import synthesize_due_monthly

    started = time.time()
    total = synthesize_due_monthly(
        limit=args.limit,
        months=args.month,
        force=args.force,
        dry_run=args.dry_run,
        progress=print,
    )
    elapsed = time.time() - started
    verb = "would synthesize" if args.dry_run else "wrote"
    print(f"Backfill complete: {verb} {total} synthesis(es) in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())