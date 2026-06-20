"""Бэкфилл base_meter_price → ₽/м² от ИТОГОВОЙ цены со скидкой.

С 2026-06-20 `база_за_м²` (snapshots.base_meter_price) = round(promo_price /
area) — цена со скидкой за м² (раньше считалась от списочной old_price). Новые
сканы пишут так через build_rows; этот скрипт ПЕРЕсчитывает уже накопленную
историю, чтобы витрина показывала скидочный ₽/м² «везде», а не только в свежих
срезах. Полностью обратим: значение всегда выводимо из promo_price/area.

    python -m bin.backfill_meter_price --db data/kvadstat.db --refresh

--refresh пересобирает материализованные витрины (today_all и т.д.), иначе они
останутся со старыми значениями до следующего скана.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from kvadstat.store import refresh_materialized


def backfill(conn: sqlite3.Connection) -> tuple[int, int]:
    """→ (updated, nulled). Пересчитывает base_meter_price из promo_price/area."""
    cur = conn.cursor()
    cur.execute("BEGIN")
    # ₽/м² = round(итоговой цены / площадь). area берём из flats по flat_id.
    cur.execute(
        """
        UPDATE snapshots
        SET base_meter_price = CAST(ROUND(
            promo_price * 1.0 /
            (SELECT f.area FROM flats f WHERE f.id = snapshots.flat_id)
        ) AS INTEGER)
        WHERE promo_price IS NOT NULL
          AND (SELECT f.area FROM flats f WHERE f.id = snapshots.flat_id) > 0
        """
    )
    updated = cur.rowcount
    # Без итоговой цены/площади ₽/м² не определён — обнуляем (как build_rows).
    cur.execute(
        """
        UPDATE snapshots
        SET base_meter_price = NULL
        WHERE base_meter_price IS NOT NULL
          AND (promo_price IS NULL
               OR COALESCE((SELECT f.area FROM flats f
                            WHERE f.id = snapshots.flat_id), 0) <= 0)
        """
    )
    nulled = cur.rowcount
    conn.commit()
    return updated, nulled


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True, type=Path)
    p.add_argument("--refresh", action="store_true",
                   help="пересобрать материализованные витрины после бэкфилла")
    args = p.parse_args(argv)
    conn = sqlite3.connect(args.db)
    try:
        updated, nulled = backfill(conn)
        print(f"base_meter_price: пересчитано {updated}, обнулено {nulled}")
        if args.refresh:
            refresh_materialized(conn)
            print("материализованные витрины пересобраны")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
