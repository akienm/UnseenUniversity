#!/usr/bin/env python3
"""
Carve a stratified 20% holdout from competition.memories.

Marks ~20% of rows per memory_type as holdout=true. The holdout set is
used exclusively by the eval harness — neither classifier sees these rows
during training.

Safe to re-run: clears all existing holdout marks before re-carving, so
the split is always fresh and reproducible for the current data set.

Usage:
    python lab/competition/carve_holdout.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os

import psycopg2

_DB_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

HOLDOUT_FRACTION = 0.20


def carve(dry_run: bool = False) -> dict[str, dict]:
    """Stratified 20% holdout split per memory_type.

    Returns {memory_type: {total, holdout, train}} for each type.
    """
    conn = psycopg2.connect(_DB_URL)
    results: dict[str, dict] = {}
    try:
        with conn:
            with conn.cursor() as cur:
                # Get distinct memory types and their row IDs, ordered
                # deterministically so the split is reproducible.
                cur.execute(
                    "SELECT DISTINCT memory_type FROM competition.memories "
                    "WHERE memory_type IS NOT NULL "
                    "  AND holdout = false OR holdout IS NULL "
                    "ORDER BY memory_type"
                )
                types = [r[0] for r in cur.fetchall()]

                if not types:
                    print("competition.memories is empty — nothing to carve.")
                    return {}

                # Reset all holdout marks first (idempotent re-run).
                if not dry_run:
                    cur.execute(
                        "UPDATE competition.memories SET holdout = false "
                        "WHERE holdout = true"
                    )

                for mtype in types:
                    # Fetch IDs ordered by insertion order (id is text timestamp-based,
                    # but string-sort gives a stable order independent of DB internals).
                    cur.execute(
                        "SELECT id FROM competition.memories "
                        "WHERE memory_type = %s "
                        "ORDER BY id",
                        (mtype,),
                    )
                    ids = [r[0] for r in cur.fetchall()]
                    total = len(ids)
                    n_holdout = max(1, round(total * HOLDOUT_FRACTION))

                    # Take every Nth row for stratified sampling (deterministic).
                    # This evenly distributes holdout rows across the ordered set,
                    # avoiding cluster artifacts from taking the first/last N%.
                    step = max(1, total // n_holdout)
                    holdout_ids = ids[::step][:n_holdout]
                    n_holdout = len(holdout_ids)

                    if not dry_run:
                        cur.executemany(
                            "UPDATE competition.memories SET holdout = true WHERE id = %s",
                            [(hid,) for hid in holdout_ids],
                        )

                    results[mtype] = {
                        "total": total,
                        "holdout": n_holdout,
                        "train": total - n_holdout,
                        "holdout_pct": round(100 * n_holdout / total, 1) if total else 0,
                    }

    finally:
        conn.close()

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be marked without writing",
    )
    args = parser.parse_args()

    prefix = "[DRY RUN] " if args.dry_run else ""
    results = carve(dry_run=args.dry_run)

    if not results:
        return

    print(f"\n{prefix}Holdout split (target {HOLDOUT_FRACTION*100:.0f}% per type):")
    total_all = sum(v["total"] for v in results.values())
    holdout_all = sum(v["holdout"] for v in results.values())

    for mtype, stats in sorted(results.items()):
        print(
            f"  {mtype:20s}: {stats['total']:5d} total → "
            f"{stats['holdout']:4d} holdout ({stats['holdout_pct']}%), "
            f"{stats['train']:4d} train"
        )

    print(
        f"\n  {'TOTAL':20s}: {total_all:5d} total → "
        f"{holdout_all:4d} holdout ({100*holdout_all//total_all}%), "
        f"{total_all-holdout_all:4d} train"
    )

    if not args.dry_run:
        print(f"\n{prefix}Holdout marks written to competition.memories.")


if __name__ == "__main__":
    main()
