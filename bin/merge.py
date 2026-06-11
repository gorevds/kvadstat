"""CLI: слить несколько SQLite от агентов в основную БД."""
import argparse
import logging
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from kvadstat.merge import merge_databases
from kvadstat.store import refresh_materialized


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True, type=Path, help="основная БД-приёмник")
    p.add_argument("sources", nargs="+", type=Path, help="источники (.db файлы)")
    args = p.parse_args(argv)

    summary = merge_databases(main_path=args.db, source_paths=args.sources)
    for src, stats in summary.items():
        print(f"{src}: +{stats['flats_in_source']} flats, +{stats['snapshots_in_source']} snapshots")
    # материализованные витрины (today_all и т.п.) иначе остались бы stale
    # до следующего ночного скана
    with closing(sqlite3.connect(args.db)) as conn:
        conn.execute("PRAGMA busy_timeout=30000")
        refresh_materialized(conn)
    print("materialized views refreshed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
