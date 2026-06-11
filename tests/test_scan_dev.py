"""Тесты bin/scan_dev.py: параллельный обход застройщиков."""
import sqlite3

import pytest

from bin import scan_dev
from kvadstat.sources.base import CollectResult, NormBlock, NormFlat, SourceError


def _fake_result(tag: str) -> CollectResult:
    return CollectResult(
        blocks=[NormBlock(native_id=f"{tag}-zhk", name=f"ЖК {tag}", slug=tag)],
        flats=[NormFlat(native_id=f"{tag}-1", native_block_id=f"{tag}-zhk",
                        rooms=1, area=40.0, floor=3, price=10_000_000)],
    )


def test_run_sweep_writes_every_developer(tmp_path, monkeypatch):
    db = tmp_path / "multi.db"
    monkeypatch.setattr(scan_dev, "SOURCES", {
        "ГК ФСК": lambda: _fake_result("fsk"),
        "Донстрой": lambda: _fake_result("don"),
        "А101": lambda: _fake_result("a101"),
    })
    failed = scan_dev.run_sweep(
        db, ["ГК ФСК", "Донстрой", "А101"],
        scan_date="2026-05-22", scan_ts="t", workers=3,
    )
    assert failed == 0
    conn = sqlite3.connect(db)
    devs = {r[0] for r in conn.execute("SELECT developer FROM blocks")}
    assert devs == {"ГК ФСК", "Донстрой", "А101"}
    assert conn.execute("SELECT COUNT(*) FROM flats").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 3
    conn.close()


def test_run_sweep_counts_failed_sources(tmp_path, monkeypatch):
    def boom():
        raise SourceError("источник недоступен")

    monkeypatch.setattr(scan_dev, "SOURCES", {
        "ГК ФСК": lambda: _fake_result("fsk"),
        "Самолёт": boom,
    })
    failed = scan_dev.run_sweep(
        tmp_path / "m.db", ["ГК ФСК", "Самолёт"],
        scan_date="d", scan_ts="t", workers=2,
    )
    assert failed == 1
    conn = sqlite3.connect(tmp_path / "m.db")
    # успешный застройщик записан, упавший — нет
    assert conn.execute("SELECT COUNT(*) FROM flats").fetchone()[0] == 1
    conn.close()


def test_main_rejects_unknown_developer(tmp_path):
    with pytest.raises(SystemExit):
        scan_dev.main(["--db", str(tmp_path / "x.db"), "--developer", "Неведомый"])


def test_main_succeeds_when_all_developers_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_dev, "run_sweep", lambda *a, **k: 0)
    monkeypatch.setattr(scan_dev, "SOURCES", dict.fromkeys("abcde"))
    rc = scan_dev.main(["--db", str(tmp_path / "x.db"), "--all"])
    assert rc == 0


def test_main_tolerates_one_flaky_source(tmp_path, monkeypatch):
    # При 10 источниках 1 флапнувший (~10%) терпим — Инград/MR Group
    # с ServicePipe регулярно отваливаются по таймауту, но юнит должен
    # оставаться green. Вчерашний снимок этого застройщика остаётся.
    monkeypatch.setattr(scan_dev, "run_sweep", lambda *a, **k: 1)
    monkeypatch.setattr(scan_dev, "SOURCES", dict.fromkeys("abcdefghij"))  # 10
    rc = scan_dev.main(["--db", str(tmp_path / "x.db"), "--all"])
    assert rc == 0  # 1 of 10 = ниже 20% threshold


def test_main_fails_on_many_failures(tmp_path, monkeypatch):
    # 3 of 10 (30%) — что-то реально не так с сетью/прокси, юнит red
    monkeypatch.setattr(scan_dev, "run_sweep", lambda *a, **k: 3)
    monkeypatch.setattr(scan_dev, "SOURCES", dict.fromkeys("abcdefghij"))
    rc = scan_dev.main(["--db", str(tmp_path / "x.db"), "--all"])
    assert rc == 1


def test_sources_registry_within_developer_registry():
    """Каждый источник обязан быть зарегистрирован в kvadstat.developers."""
    from kvadstat.developers import DEVELOPERS

    assert set(scan_dev.SOURCES) <= set(DEVELOPERS)


def _scan_runs_row(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT status, error_msg, n_flats, n_rejected FROM scan_runs"
        ).fetchone()
    finally:
        conn.close()


def test_run_developer_zero_flats_is_partial(tmp_path, monkeypatch):
    """Источник вернул 0 квартир без исключения (дрейф API) — это НЕ 'ok'."""
    db = tmp_path / "empty.db"
    monkeypatch.setattr(scan_dev, "SOURCES", {"ГК ФСК": lambda: CollectResult()})
    scan_dev._ensure_schema(db)
    scan_dev.run_developer(db, "ГК ФСК", scan_date="2026-06-11", scan_ts="t")
    status, msg, n_flats, _ = _scan_runs_row(db)
    assert status == "partial"
    assert n_flats == 0
    assert "0 квартир" in msg


def test_run_developer_mass_rejection_is_partial(tmp_path, monkeypatch):
    """Гейт отбраковал большинство квартир (смена единиц/формата) — 'partial'.

    9 из 10 квартир в гео-невалидном ЖК (нулевые координаты ≈ 7000 км от
    Москвы) → n_rejected=9 > 20% → день нельзя считать полным.
    """
    bad_block = NormBlock(native_id="bad", name="Плохой", slug="bad",
                          meta={"latitude": 0.0, "longitude": 0.0})
    good_block = NormBlock(native_id="good", name="Хороший", slug="good")
    flats = [NormFlat(native_id=f"b{i}", native_block_id="bad", rooms=1,
                      area=40.0, floor=2, price=10_000_000) for i in range(9)]
    flats.append(NormFlat(native_id="g1", native_block_id="good", rooms=1,
                          area=40.0, floor=2, price=10_000_000))
    res = CollectResult(blocks=[bad_block, good_block], flats=flats)
    db = tmp_path / "rej.db"
    monkeypatch.setattr(scan_dev, "SOURCES", {"ГК ФСК": lambda: res})
    scan_dev._ensure_schema(db)
    scan_dev.run_developer(db, "ГК ФСК", scan_date="2026-06-11", scan_ts="t")
    status, msg, n_flats, n_rejected = _scan_runs_row(db)
    assert (n_flats, n_rejected) == (1, 9)
    assert status == "partial"
    assert "отбраковано" in msg


def test_main_fails_at_exactly_twenty_pct(tmp_path, monkeypatch):
    # Ровно 2 из 10 (порог 20%) — уже red. NB: main() сам инжектит 'ПИК'
    # в SOURCES, поэтому даём 9 ключей: 9 + ПИК = ровно 10 застройщиков.
    monkeypatch.setattr(scan_dev, "run_sweep", lambda *a, **k: 2)
    monkeypatch.setattr(scan_dev, "SOURCES", dict.fromkeys("abcdefghi"))
    rc = scan_dev.main(["--db", str(tmp_path / "x.db"), "--all"])
    assert rc == 1


def test_run_developer_mass_nullified_prices_is_partial(tmp_path, monkeypatch):
    """NULL-цена у большинства снапшотов (копейки/смена формата) → 'partial'."""
    flats = [NormFlat(native_id=f"h{i}", native_block_id="z", rooms=1,
                      area=40.0, floor=2, price=None) for i in range(8)]
    flats.append(NormFlat(native_id="ok", native_block_id="z", rooms=1,
                          area=40.0, floor=2, price=10_000_000))
    res = CollectResult(blocks=[NormBlock(native_id="z", name="Z", slug="z")],
                        flats=flats)
    db = tmp_path / "mn.db"
    monkeypatch.setattr(scan_dev, "SOURCES", {"ГК ФСК": lambda: res})
    scan_dev._ensure_schema(db)
    scan_dev.run_developer(db, "ГК ФСК", scan_date="2026-06-11", scan_ts="t")
    status, msg, n_flats, _ = _scan_runs_row(db)
    assert n_flats == 9          # все лоты записаны (присутствие сохранено)
    assert status == "partial"
    assert "price=NULL" in msg
