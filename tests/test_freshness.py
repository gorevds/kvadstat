"""Тесты bin/check_freshness.py: алерт на застойные/умершие источники."""
import sqlite3

from bin.check_freshness import check_freshness
from kvadstat.store import apply_schema


def _db(tmp_path):
    p = tmp_path / "f.db"
    c = sqlite3.connect(p)
    apply_schema(c)
    c.close()
    return p


def _run(db, dev, date, status="ok"):
    c = sqlite3.connect(db)
    c.execute(
        "INSERT OR REPLACE INTO scan_runs "
        "(scan_date, scan_ts, developer, status) VALUES (?,?,?,?)",
        (date, date + "T06:00:00+03:00", dev, status),
    )
    c.commit()
    c.close()


def test_fresh_developer_passes(tmp_path):
    db = _db(tmp_path)
    _run(db, "ПИК", "2026-06-10")
    problems = check_freshness(db, max_age_days=2, today="2026-06-11")
    assert problems == []


def test_stale_developer_flagged(tmp_path):
    db = _db(tmp_path)
    _run(db, "ПИК", "2026-06-10")
    _run(db, "Инград", "2026-06-05")  # 6 дней назад — застой
    problems = check_freshness(db, max_age_days=2, today="2026-06-11")
    assert len(problems) == 1
    assert "Инград" in problems[0]


def test_last_run_error_flagged_even_if_fresh(tmp_path):
    db = _db(tmp_path)
    _run(db, "ПИК", "2026-06-10")
    _run(db, "MR Group", "2026-06-11", status="error")
    problems = check_freshness(db, max_age_days=2, today="2026-06-11")
    assert len(problems) == 1
    assert "MR Group" in problems[0] and "error" in problems[0]


def test_partial_is_fresh_not_flagged(tmp_path):
    # partial — деградация, но данные идут; алертит scan_runs-аналитика,
    # а freshness ловит именно «совсем пропал»
    db = _db(tmp_path)
    _run(db, "ГК ФСК", "2026-06-11", status="partial")
    assert check_freshness(db, max_age_days=2, today="2026-06-11") == []


def test_developer_gone_from_window_silenced(tmp_path):
    # источник, выведенный из эксплуатации >30 дней назад, не алертит вечно
    db = _db(tmp_path)
    _run(db, "ПИК", "2026-06-10")
    _run(db, "Старый", "2026-04-01")
    problems = check_freshness(db, max_age_days=2, today="2026-06-11")
    assert problems == []


def test_empty_db_is_a_problem(tmp_path):
    db = _db(tmp_path)
    problems = check_freshness(db, max_age_days=2, today="2026-06-11")
    assert problems and "scan_runs" in problems[0]


def test_suspended_developer_not_flagged(tmp_path):
    """Снятый с обхода источник (нет в active) не алертит старыми error."""
    db = _db(tmp_path)
    _run(db, "ПИК", "2026-06-11")
    _run(db, "Level", "2026-06-11", status="error")  # вчерашний error снятого
    # active без Level — как после приостановки в scan_dev.SOURCES
    problems = check_freshness(db, max_age_days=2, today="2026-06-11",
                               active={"ПИК"})
    assert problems == []


def test_active_default_excludes_suspended_level(tmp_path):
    """По умолчанию active = scan_dev.SOURCES, где Level больше нет."""
    db = _db(tmp_path)
    _run(db, "Level", "2026-06-11", status="error")
    _run(db, "ПИК", "2026-06-11")
    assert check_freshness(db, max_age_days=2, today="2026-06-11") == []
