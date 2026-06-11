"""Live-смоук: по одному МИНИМАЛЬНОМУ реальному запросу на источник.

Цель — поймать дрейф формата API раньше прода: фикстуры замораживаются
(2026-05) и не видят, что застройщик переименовал ключ. Здесь проверяется
только верхнеуровневая форма ответа, не содержимое.

По умолчанию НЕ запускаются (addopts = -m 'not live' в pyproject):
    pytest -m live                 # все источники
    pytest -m live -k fsk          # один

Сетевые сбои/анти-бот на корп-сети ожидаемы: HTTP-уровневые отказы
(timeout, 404-страница вместо JSON) → skip, а не fail. Падение теста
означает именно ДРЕЙФ ФОРМАТА: JSON пришёл, но ключи уехали. Гонять
еженедельно с СЕРВЕРА (там все хосты доступны):
    /opt/kvadstat/venv/bin/python -m pytest tests/test_live_smoke.py -m live
"""
from __future__ import annotations

import pytest
import requests

from kvadstat.sources.base import make_session, request_json

pytestmark = pytest.mark.live

_TIMEOUT = 30.0


def _get(url, **kw):
    from kvadstat.sources.base import SourceError
    s = make_session()
    try:
        return request_json(s, kw.pop("method", "GET"), url, timeout=_TIMEOUT, **kw)
    except (requests.ConnectionError, requests.Timeout) as exc:
        pytest.skip(f"хост недоступен из этой сети: {exc}")
    except SourceError as exc:
        # HTTP-уровневый отказ (анти-бот/гео-блок/таймаут сквозь ретраи) —
        # не дрейф формата; с сервера такой же ответ дал бы alert через
        # scan_runs. Скипаем, чтобы сьют был полезен с любой сети.
        pytest.skip(f"HTTP-уровневый отказ (не формат): {exc}")


def test_live_pik_flat_api():
    data = _get("https://api.pik.ru/v2/flat",
                params={"block_id": 1165, "types": 1, "page": 1})
    # payload — объект с block + flats (см. client.fetch_block_flats)
    assert "block" in data, "v2/flat: пропал ключ block"
    flats = data.get("flats") or []
    if not flats:
        pytest.skip("блок 1165 без квартир в продаже — форму списка не проверить")
    assert "id" in flats[0] and "price" in flats[0]


def test_live_fsk_complexes():
    from kvadstat.sources.fsk import _COMPLEX_URL
    data = _get(_COMPLEX_URL)
    items = data if isinstance(data, list) else (data.get("data") or [])
    assert items and "slug" in items[0] and "city_id" in items[0]


def test_live_a101_flats_page():
    from kvadstat.sources.a101 import _FLATS_URL
    data = _get(_FLATS_URL, params={"limit": 1, "offset": 0})
    assert "results" in data
    if data["results"]:
        fl = data["results"][0]
        assert "id" in fl and "project_slug" in fl


def test_live_level_flats_page():
    from kvadstat.sources.level import _FLATS_URL
    data = _get(_FLATS_URL, params={"limit": 1, "offset": 0})
    assert "results" in data


def test_live_donstroy_flats_page():
    from kvadstat.sources.donstroy import _FLATS_URL
    data = _get(_FLATS_URL, method="POST", json={"page": 1})
    assert "flats" in data and data["flats"], "ключ flats пропал/пуст — дрейф API"


def test_live_absolut_graphql():
    from kvadstat.sources.absolut import _ALL_FLATS_QUERY, _GRAPHQL_URL
    s = make_session()
    s.headers.update({"Origin": "https://www.absrealty.ru"})
    try:
        data = request_json(s, "POST", _GRAPHQL_URL, timeout=_TIMEOUT, json={
            "operationName": "allFlats", "query": _ALL_FLATS_QUERY,
            "variables": {"first": 1, "after": None, "orderBy": "pk"}})
    except (requests.ConnectionError, requests.Timeout) as exc:
        pytest.skip(f"хост недоступен: {exc}")
    conn = (data.get("data") or {}).get("allFlats") or {}
    assert conn.get("edges"), "GraphQL allFlats без edges"
    assert isinstance(conn.get("totalCount"), int)


def test_live_granel_flats_page():
    from kvadstat.sources.granel import _FLATS_URL
    data = _get(_FLATS_URL, params={"limit": 1, "offset": 0})
    assert "results" in data


def test_live_ingrad_flats_page():
    from kvadstat.sources.ingrad import _FLATS_URL as _U
    data = _get(_U, params={"numberElementsPage": 1, "page": 1, "type": "flat"})
    assert "list" in data
    # заодно фиксируем семантику allCount (см. TODO(live-check) в ingrad.py)
    assert "allCount" in data, "allCount пропал — обновить totals-проверку"


def test_live_brusnika_moskva_page():
    data = _get("https://moskva.brusnika.ru/api/filter/flats/",
                params={"limit": 1, "offset": 0})
    assert "results" in data


def test_live_mrgroup_first_zhk():
    from kvadstat.sources.mrgroup import MR_BLOCKS, _fetch_page, parse_flats_page
    s = requests.Session()
    s.headers.update({"User-Agent":
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"})
    slug = next(iter(MR_BLOCKS))
    try:
        html = _fetch_page(s, slug)
    except Exception as exc:  # noqa: BLE001 — анти-бот/сеть → skip, не fail
        pytest.skip(f"mr-group недоступен: {exc}")
    flats = parse_flats_page(html, slug)
    assert flats, "0 карточек: анти-бот или смена вёрстки"
