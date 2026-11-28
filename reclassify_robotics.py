"""One-time reclassification of archived articles into Robotics & Embodied AI.

The 8th theme was added 2026-11-28 because ~128 robotics articles were
scattered across the other themes (e.g. "Gemini Robotics 2.5" landed in
Governance). This script moves archived articles whose TITLE+SUMMARY now
confidently keyword-classify into the new theme — conservative by design:
only Gate-1 keyword wins move; ambiguous articles stay where they are.

Also updates the local classification-cache mirror (so offline runs don't
revert the moves), backfills the theme's first-appearance record, and
removes the Test Source / Test Theme residue row.

Usage:
    python reclassify_robotics.py --dry-run   # report candidates, no writes
    python reclassify_robotics.py             # apply moves + cleanup

Idempotent: re-running finds 0 candidates once the archive is moved.
"""
import argparse
import time
from collections import Counter

from core.logger import setup_logger

logger = setup_logger(__name__)

ROBOTICS = "Robotics & Embodied AI"


def main() -> int:
    parser = argparse.ArgumentParser(description="Reclassify archived robotics articles (Phase 6 follow-up).")
    parser.add_argument("--dry-run", action="store_true", help="report candidates without writing")
    args = parser.parse_args()

    from core.classifier import keyword_classify
    from core.supabase_client import get_supabase_manager

    manager = get_supabase_manager()
    if not manager.is_available():
        print("Supabase unavailable — nothing to do.")
        return 1

    started = time.time()

    # ---- Fetch the archive in pages ----
    articles = []
    page = 1000
    offset = 0
    while True:
        resp = manager.client.table("articles")\
            .select("id, run_id, title, summary, theme_name, content_hash")\
            .range(offset, offset + page - 1)\
            .execute()
        rows = resp.data or []
        articles.extend(rows)
        if len(rows) < page:
            break
        offset += page
    print(f"Fetched {len(articles)} archived articles.")

    # ---- Conservative candidates: Gate-1 keyword win for the new theme,
    # with at least one weight-3 specialist hit (score >= 3) so incidental
    # mentions and weak tie-breaks don't move. ----
    import re as _re
    from config.themes import THEMES
    robotics_kws = THEMES[ROBOTICS]["keywords"]

    def _robotics_score(text: str) -> int:
        return sum(
            len(_re.findall(r"\b" + _re.escape(k.lower()) + r"\b", text)) * w
            for k, w in robotics_kws.items()
        )

    candidates = []
    for a in articles:
        if a.get("theme_name") == ROBOTICS:
            continue
        title, summary = a.get("title", ""), a.get("summary", "") or ""
        if keyword_classify(title, summary) == ROBOTICS and _robotics_score(f"{title} {summary}".lower()) >= 3:
            candidates.append(a)

    by_theme = Counter(a["theme_name"] for a in candidates)
    print(f"\nCandidates: {len(candidates)}")
    for theme, n in by_theme.most_common():
        print(f"  {n:>4}  from {theme}")
    print("\nSample titles:")
    for a in candidates[:15]:
        print(f"  [{a['theme_name']}] {a.get('title', '')[:90]}")

    if args.dry_run:
        print(f"\nDry run — no writes ({time.time() - started:.1f}s).")
        return 0
    if not candidates:
        print("Nothing to move.")
    else:
        # UNIQUE(content_hash, theme_name): a hash may exist under several
        # themes (cross-run duplicates), and some hashes already have a
        # Robotics row. Per hash: update ONE copy into Robotics (or none if
        # a Robotics row already exists) and delete the other misclassified
        # copies — the correctly-themed row carries the article.
        existing = manager.client.table("articles")\
            .select("content_hash").eq("theme_name", ROBOTICS).execute()
        target_hashes = {r["content_hash"] for r in (existing.data or [])}

        update_ids, delete_ids = [], []
        seen_hashes = set(target_hashes)
        for a in candidates:
            h = a.get("content_hash")
            if not h:
                update_ids.append(a["id"])
            elif h in seen_hashes:
                delete_ids.append(a["id"])
            else:
                seen_hashes.add(h)
                update_ids.append(a["id"])

        moved = 0
        # Small chunks + one retry: the sync Supabase client surfaces
        # transient WinError 10035 socket errors under rapid traffic.
        for i in range(0, len(update_ids), 25):
            chunk = update_ids[i:i + 25]
            for attempt in range(2):
                try:
                    manager.client.table("articles")\
                        .update({"theme_name": ROBOTICS}).in_("id", chunk).execute()
                    moved += len(chunk)
                    break
                except Exception as e:
                    if attempt == 0:
                        time.sleep(1.0)
                    else:
                        logger.warning("Batch update failed (%d rows): %s", len(chunk), e)
        for i in range(0, len(delete_ids), 25):
            chunk = delete_ids[i:i + 25]
            for attempt in range(2):
                try:
                    manager.client.table("articles").delete().in_("id", chunk).execute()
                    moved += len(chunk)
                    break
                except Exception as e:
                    if attempt == 0:
                        time.sleep(1.0)
                    else:
                        logger.warning("Dedupe delete failed (%d rows): %s", len(chunk), e)
        print(f"\nMoved {moved} articles into {ROBOTICS} "
              f"({len(update_ids)} updated, {len(delete_ids)} dedupe-deleted).")

        # Keep the offline classification mirror in sync so cached lookups
        # don't revert the moves on the next offline run.
        from core.classification_cache import mark_classified
        mark_classified({a["content_hash"]: ROBOTICS for a in candidates if a.get("content_hash")})

        # Backfill the theme's true first appearance from the earliest moved
        # article's run (insert-if-absent — never overwrites an existing row).
        run_ids = list({a["run_id"] for a in candidates if a.get("run_id")})
        earliest = None
        for i in range(0, len(run_ids), 100):
            resp = manager.client.table("trend_runs")\
                .select("id, run_date").in_("id", run_ids[i:i + 100]).execute()
            for r in (resp.data or []):
                if r.get("run_date") and (earliest is None or r["run_date"] < earliest["run_date"]):
                    earliest = r
        if earliest:
            evidence = next(
                (a.get("title", "") for a in candidates if a.get("run_id") == earliest["id"]), ""
            )
            manager.record_theme_first_seen(ROBOTICS, earliest["run_date"], earliest["id"], evidence)
            print(f"First appearance backfilled: {earliest['run_date']}.")

    # ---- Test-residue cleanup (approved 2026-11-28) ----
    try:
        resp = manager.client.table("articles")\
            .select("id, content_hash")\
            .or_("source_name.eq.Test Source,theme_name.eq.Test Theme")\
            .execute()
        residue = resp.data or []
        if residue:
            ids = [r["id"] for r in residue]
            manager.client.table("articles").delete().in_("id", ids).execute()
            manager.client.table("theme_summaries").delete().eq("theme_name", "Test Theme").execute()
            from core.classification_cache import mark_classified  # noqa: F811
            from core import classification_cache as cc
            data = cc._load_local_cache()
            for r in residue:
                data.pop(r.get("content_hash"), None)
            cc._save_local_cache(data)
            print(f"Removed {len(ids)} test-residue article row(s).")
    except Exception as e:
        logger.warning("Test-residue cleanup failed: %s", e)

    print(f"\nDone in {time.time() - started:.1f}s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())