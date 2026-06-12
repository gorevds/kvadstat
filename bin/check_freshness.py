"""Проверка свежести данных по застройщикам: «совсем пропал» — алерт.

Замыкает дыру наблюдаемости: exit-код kvadstat-scan терпит до 20% упавших
источников (флапы), а scan_runs со статусами 'partial'/'error' никто не
читает автоматически. Один источник может умереть навсегда (смена API) и
молча перестать давать данные — здесь это ловится по двум признакам:

  1) последний скан застройщика старше --max-age-days (данные не идут);
  2) последний скан завершился 'error' (данные шли, но сегодня сломались).

'partial' свежим НЕ считается проблемой этого чека: данные поступают,
деградацию видно в scan_runs и velocity сама исключает такие дни.

Источник списка застройщиков — сами scan_runs за последние 30 дней:
выведенный из эксплуатации источник перестаёт алертить, как только
выпадает из окна (до этого ~месяц алертит — осознанная цена простоты).

Запускается kvadstat-freshness.timer; ненулевой exit → OnFailure →
kvadstat-notify@ (Telegram/journal).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

MSK = timezone(timedelta(hours=3))
# Окно, в котором застройщик считается «эксплуатируемым».
ACTIVE_WINDOW_DAYS = 30


def _active_developers() -> set[str] | None:
    """Имена активно сканируемых застройщиков (реестр scan_dev.SOURCES).

    Нужно, чтобы приостановленный источник (Level за Qrator, см. scan_dev)
    не алертил ещё 30 дней своими старыми error-записями в scan_runs.
    None — если реестр недоступен (тогда проверяем всех, fail-safe).
    """
    try:
        from bin.scan_dev import SOURCES
        return set(SOURCES)
    except Exception:  # noqa: BLE001 — freshness не должен падать из-за импорта
        return None


def check_freshness(db_path: Path | str, *, max_age_days: int,
                    today: str | None = None,
                    active: set[str] | None = None) -> list[str]:
    """→ список человекочитаемых проблем (пусто = всё свежо).

    Проверяются только активные застройщики (`active`; по умолчанию —
    текущий scan_dev.SOURCES): снятый с обхода источник не считается
    «протухшим».
    """
    if active is None:
        active = _active_developers()
    today_d = (date.fromisoformat(today) if today
               else datetime.now(MSK).date())
    window_start = (today_d - timedelta(days=ACTIVE_WINDOW_DAYS)).isoformat()
    stale_before = (today_d - timedelta(days=max_age_days)).isoformat()

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT developer,
                   MAX(scan_date) AS last_any,
                   MAX(CASE WHEN status IN ('ok','partial')
                            THEN scan_date END) AS last_data
            FROM scan_runs
            WHERE scan_date >= ? AND developer != '_all_'
            GROUP BY developer
            """,
            (window_start,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return [f"scan_runs пуст за последние {ACTIVE_WINDOW_DAYS} дней — "
                "сканер не работает вовсе?"]

    problems: list[str] = []
    conn = sqlite3.connect(db_path)
    try:
        for dev, last_any, last_data in sorted(rows):
            if active is not None and dev not in active:
                continue  # приостановленный источник не «протух»
            last_status = conn.execute(
                "SELECT status FROM scan_runs WHERE developer=? "
                "ORDER BY scan_date DESC, scan_ts DESC LIMIT 1",
                (dev,),
            ).fetchone()[0]
            if last_status == "error":
                problems.append(
                    f"{dev}: последний скан ({last_any}) завершился error"
                )
            elif last_data is None or last_data < stale_before:
                problems.append(
                    f"{dev}: данных нет с {last_data or 'начала окна'} "
                    f"(порог {max_age_days} дн.)"
                )
    finally:
        conn.close()
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True, type=Path)
    p.add_argument("--max-age-days", type=int, default=2)
    args = p.parse_args(argv)
    problems = check_freshness(args.db, max_age_days=args.max_age_days)
    for line in problems:
        print(line)
    if problems:
        return 1
    print("freshness ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
