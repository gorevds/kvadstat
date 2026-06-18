"""Canned-query fav_flats: последний известный срез по списку id квартир.

Витрина «Избранное» показывает снятые с продажи лоты, которых уже нет в
today_all (текущий срез). fav_flats возвращает их в той же колоночной форме
(русские имена) на ПОСЛЕДНИЙ снапшот каждой запрошенной квартиры.

SQL читается прямо из metadata.yml (не дублируется здесь) — тест ловит дрейф
между задеплоенным запросом и ожиданиями.
"""
from pathlib import Path

import yaml

from kvadstat.store import apply_schema, upsert

FLAT = {
    "id": 100, "guid": "g-100", "block_id": 1165, "bulk_id": 1, "section_id": 1,
    "layout_id": 1, "bulk_name": "Корпус 1", "section_no": 2, "floor": 7,
    "rooms": "1", "rooms_fact": 1, "is_studio": 0, "area": 33.5,
    "area_kitchen": 8.0, "area_living": 16.0, "number": "1", "name": "Кв-100",
    "url": "https://ex.test/flat/100", "pdf_url": None, "plan_url": None,
    "ceiling_height": 2.75, "settlement_date": "2027-10-31T00:00:00+00:00",
    "first_seen": "2026-05-15",
}
SNAP = {
    "flat_id": 100, "scan_date": "2026-05-15", "scan_ts": "2026-05-15T06:00+03:00",
    "status": "free", "price": 12_000_000, "meter_price": 358_209,
    "base_meter_price": 358_209, "promo_price": 12_000_000, "discount_pct": 0.0,
    "has_promo": 0, "old_price": None, "discount": 0, "finish": "С отделкой",
    "mortgage_min_rate": 6.0, "mortgage_best_name": "Семейная",
    "updated_at": "2026-05-14T10:00:00+00:00",
}


def _fav_sql() -> str:
    meta = yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "metadata.yml").read_text()
    )
    return meta["databases"]["kvadstat"]["queries"]["fav_flats"]["sql"]


def _seed(conn):
    apply_schema(conn)
    # flat 100: два среза — последний 2026-05-16 по 11.9M
    upsert(conn, flats=[FLAT], snapshots=[SNAP])
    upsert(conn, flats=[FLAT],
           snapshots=[dict(SNAP, scan_date="2026-05-16", price=11_900_000)])
    # flat 200 (другой ЖК): один срез 2026-05-15 по 9M
    flat_b = dict(FLAT, id=200, guid="g-200", block_id=999, name="Кв-200")
    upsert(conn, flats=[flat_b],
           snapshots=[dict(SNAP, flat_id=200, price=9_000_000)])


def test_fav_flats_returns_latest_snapshot_per_id(conn):
    _seed(conn)
    rows = conn.execute(_fav_sql(), {"ids": "[100, 200]"}).fetchall()
    cols = [c[0] for c in conn.execute(_fav_sql(), {"ids": "[100]"}).description]
    assert "жк" in cols and "дата_среза" in cols and "block_id" in cols
    by_id = {r[cols.index("id")]: r for r in rows}
    assert set(by_id) == {100, 200}
    # flat 100 — именно последний срез (11.9M, 2026-05-16), не первый
    assert by_id[100][cols.index("базовая_цена")] == 11_900_000
    assert by_id[100][cols.index("дата_среза")] == "2026-05-16"
    assert by_id[200][cols.index("базовая_цена")] == 9_000_000
    # русская проекция комнатности
    assert by_id[100][cols.index("комнат")] == "1к"


def test_fav_flats_filters_by_id_list(conn):
    _seed(conn)
    rows = conn.execute(_fav_sql(), {"ids": "[100]"}).fetchall()
    assert len(rows) == 1


def test_fav_flats_empty_list_returns_nothing(conn):
    _seed(conn)
    rows = conn.execute(_fav_sql(), {"ids": "[]"}).fetchall()
    assert rows == []
