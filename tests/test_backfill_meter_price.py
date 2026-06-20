"""Бэкфилл base_meter_price пересчитывает историю из promo_price/area."""
from bin.backfill_meter_price import backfill
from kvadstat.store import apply_schema, upsert

FLAT = {
    "id": 100, "guid": "g-100", "block_id": 1, "bulk_id": 1, "section_id": 1,
    "layout_id": 1, "bulk_name": "К1", "section_no": 1, "floor": 5, "rooms": "1",
    "rooms_fact": 1, "is_studio": 0, "area": 42.9, "area_kitchen": 8.0,
    "area_living": 16.0, "number": "1", "name": "Кв", "url": "u", "pdf_url": None,
    "plan_url": None, "ceiling_height": 2.7, "settlement_date": None,
    "first_seen": "2026-05-01",
}


def _snap(date, *, price, promo, base_meter):
    return {
        "flat_id": 100, "scan_date": date, "scan_ts": date + "T06:00+03:00",
        "status": "free", "price": price, "meter_price": None,
        "base_meter_price": base_meter, "promo_price": promo,
        "discount_pct": 7.0, "has_promo": 1, "old_price": None, "discount": 0,
        "finish": "С отделкой", "mortgage_min_rate": 6.0,
        "mortgage_best_name": "X", "updated_at": "2026-05-01T10:00:00+00:00",
    }


def test_backfill_recomputes_from_promo_over_area(conn):
    apply_schema(conn)
    # base_meter намеренно записан по СТАРОЙ семантике (price/area = 476_200);
    # должен стать round(promo/area) = round(18_998_951/42.9) = 442_866.
    upsert(conn, flats=[FLAT],
           snapshots=[_snap("2026-05-15", price=20_428_980,
                            promo=18_998_951, base_meter=476_200)])
    updated, nulled = backfill(conn)
    assert updated == 1
    val = conn.execute(
        "SELECT base_meter_price FROM snapshots WHERE flat_id=100"
    ).fetchone()[0]
    assert val == round(18_998_951 / 42.9)  # 442_866


def test_backfill_nulls_when_promo_missing(conn):
    apply_schema(conn)
    upsert(conn, flats=[FLAT],
           snapshots=[_snap("2026-05-16", price=None, promo=None,
                            base_meter=999_999)])
    _, nulled = backfill(conn)
    assert nulled == 1
    val = conn.execute(
        "SELECT base_meter_price FROM snapshots WHERE flat_id=100"
    ).fetchone()[0]
    assert val is None


def test_backfill_is_idempotent(conn):
    apply_schema(conn)
    upsert(conn, flats=[FLAT],
           snapshots=[_snap("2026-05-15", price=20_428_980,
                            promo=18_998_951, base_meter=476_200)])
    backfill(conn)
    first = conn.execute(
        "SELECT base_meter_price FROM snapshots WHERE flat_id=100"
    ).fetchone()[0]
    backfill(conn)
    second = conn.execute(
        "SELECT base_meter_price FROM snapshots WHERE flat_id=100"
    ).fetchone()[0]
    assert first == second == 442_866
