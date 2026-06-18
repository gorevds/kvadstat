"""Браузерный тест модуля избранного (static/favorites.js).

Проверяет публичный контракт window.KV.fav в реальном браузере (localStorage,
персистентность между перезагрузками, синхронизация между вкладками через
storage-событие). Полный UI-флоу (кнопка в панели, вкладка, подсветка)
проверяется вживую playwright'ом — здесь фиксируется ядро-хранилище.

Требует playwright + установленный chromium; без них — skip (как live-тесты).
Запуск:  pytest tests/test_favorites_ui.py
"""
from __future__ import annotations

import shutil
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

STATIC = Path(__file__).resolve().parent.parent / "static"
HARNESS = """<!doctype html><meta charset=utf-8>
<script src="/common.js"></script>
<script src="/favorites.js"></script>
"""


@pytest.fixture
def served(tmp_path):
    """Поднимает http-сервер с common.js + favorites.js + страницей-хостом."""
    shutil.copy(STATIC / "common.js", tmp_path / "common.js")
    shutil.copy(STATIC / "favorites.js", tmp_path / "favorites.js")
    (tmp_path / "index.html").write_text(HARNESS, encoding="utf-8")
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@pytest.fixture
def browser():
    try:
        with sync_api.sync_playwright() as p:
            b = p.chromium.launch()
            yield b
            b.close()
    except sync_api.Error as e:  # браузер не установлен
        pytest.skip(f"chromium недоступен: {e}")


def test_fav_add_toggle_count(served, browser):
    pg = browser.new_page()
    pg.goto(served)
    assert pg.evaluate("KV.fav.count()") == 0
    assert pg.evaluate("KV.fav.toggle(123)") is True   # добавили
    assert pg.evaluate("KV.fav.has(123)") is True
    assert pg.evaluate("KV.fav.count()") == 1
    assert pg.evaluate("KV.fav.toggle(123)") is False  # сняли
    assert pg.evaluate("KV.fav.has(123)") is False
    assert pg.evaluate("KV.fav.count()") == 0
    pg.close()


def test_fav_persists_across_reload(served, browser):
    pg = browser.new_page()
    pg.goto(served)
    pg.evaluate("KV.fav.add(7); KV.fav.add(8)")
    pg.reload()
    assert sorted(pg.evaluate("KV.fav.list()")) == [7, 8]
    pg.close()


def test_fav_ignores_garbage_ids(served, browser):
    pg = browser.new_page()
    pg.goto(served)
    pg.evaluate("KV.fav.add('x'); KV.fav.add(NaN); KV.fav.add(undefined)")
    assert pg.evaluate("KV.fav.count()") == 0
    pg.close()


def test_fav_onchange_fires_across_tabs(served, browser):
    """Изменение в одной вкладке → onChange в другой (storage-событие)."""
    ctx = browser.new_context()
    a = ctx.new_page()
    a.goto(served)
    b = ctx.new_page()
    b.goto(served)
    b.evaluate("window.__hits = 0; KV.fav.onChange(() => { window.__hits++; })")
    a.evaluate("KV.fav.add(42)")
    b.wait_for_function("window.__hits > 0", timeout=5000)
    assert b.evaluate("KV.fav.has(42)") is True
    ctx.close()
