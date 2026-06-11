"""Источник данных Группа Гранель (granelle.ru).

Открытый Django REST API: `/api/flats/` отдаёт квартиры limit/offset
пагинацией (~2700 квартир, 32 ЖК — преимущественно Москва+МО+Уфа).
Поля чистые: явный `finish_type`, отдельно `price` (base) и
`current_price` (со скидкой), `coords` ЖК — строкой "lat,lng".
"""
from __future__ import annotations

import logging

import requests

from kvadstat.geo import CITY_CENTERS, haversine_km
from kvadstat.sources.base import (
    CollectResult,
    NormBlock,
    NormFlat,
    make_session,
    norm_status,
    parse_coords,
    request_json,
    safe_next_url,
    settlement_label,
    to_int,
    totals_deficit,
)

DEVELOPER = "Гранель"
_FLATS_URL = "https://granelle.ru/api/flats/"
_PROJECTS_URL = "https://granelle.ru/api/projects/"
_PAGE_LIMIT = 200
_MAX_PAGES = 30        # 30 × 200 = 6000 квартир запас (сейчас ~2700)

log = logging.getLogger("kvadstat.sources.granel")

# finish_type API → человеческое. Подтверждено live-выборкой:
# Гранель реально отдаёт «without_finish» / «whitebox» / «finish»
# (НЕ «with_finish» / «white_box» — _ в середине нет, префиксов нет).
_FINISH_MAP = {
    "without_finish":   "Без отделки",
    "whitebox":         "WhiteBox",
    "white_box":        "WhiteBox",      # на всякий случай
    "finish":           "С отделкой",
    "with_finish":      "С отделкой",
    "with_furniture":   "С отделкой и мебелью",
    "rough_finish":     "Предчистовая отделка",
}


_to_int = to_int  # общий парсер (kvadstat.sources.base)


_coords = parse_coords  # общий парсер (kvadstat.sources.base)


def _settlement(fl: dict) -> str | None:
    """«4 кв. 2025» из completion_quarter + completion_year (оба строки)."""
    return settlement_label(fl.get("completion_quarter"), fl.get("completion_year"))


def _to_norm(fl: dict) -> NormFlat:
    # current_price — final (со скидкой если есть); price — base. Берём final.
    current = fl.get("current_price")
    base = fl.get("price")
    # API отдаёт price как float/Decimal-как-строку — API отдаёт рубли дробью — округляем до int рублей
    def _money(v):
        if v is None: return None
        try: return int(round(float(v)))
        except (TypeError, ValueError): return None
    price = _money(current) if current is not None else _money(base)
    old_price = None
    base_int = _money(base)
    if fl.get("show_price_discounted") and base_int and price and base_int > price:
        old_price = base_int
    building = fl.get("building")
    area = fl.get("area")
    return NormFlat(
        native_id=fl.get("id"),
        native_block_id=fl.get("project_slug") or fl.get("project"),
        # rooms=0 — студия (API даёт 0); rooms может быть 0/1/2/3/4/5
        rooms=fl.get("rooms"),
        area=area,
        floor=_to_int(fl.get("floor")),
        price=price,
        meter_price=(round(price / area) if price and area else None),
        old_price=old_price,
        # status: 1 = в продаже, прочие — забронированы/проданы. API уже
        # фильтрует чем-то — берём «free» для всех результатов.
        status=norm_status(fl.get("status"), {1}, source="Гранель"),
        bulk_name=(f"Корпус {building}" if building not in (None, "") else None),
        section_no=_to_int(fl.get("section")),
        settlement_date=_settlement(fl),
        # per-flat URL: проверено live — нужен ПОЛНЫЙ путь с project_slug.
        # `/flat/<id>` и `/flats/<id>` оба дают 404; работает только
        # `/flats/<slug>/<id>` (без trailing slash).
        url=(f"https://granelle.ru/flats/{fl['project_slug']}/{fl['id']}"
             if (fl.get("id") and fl.get("project_slug")) else None),
        # Неизвестный finish_type → None (не показываем сырой en-snake-case
        # в UI). Если появится новое значение — увидим в логе с warning.
        finish=_FINISH_MAP.get(fl.get("finish_type") or ""),
        number=str(fl["number"]) if fl.get("number") is not None else None,
        plan_url=fl.get("plan") or fl.get("plan_png"),
    )


def _fetch_project_meta(session: requests.Session) -> dict[str, dict]:
    """{slug → meta} с координатами + метро + кол-вом этажей. ОДИН запрос."""
    out: dict[str, dict] = {}
    try:
        payload = request_json(session, "GET", _PROJECTS_URL)
    except Exception as exc:  # noqa: BLE001
        log.warning("Гранель: список проектов не получен: %s", exc)
        return out
    items = payload if isinstance(payload, list) else (payload.get("results") or [])
    for p in items:
        slug = p.get("slug")
        if not slug: continue
        meta: dict = {}
        if (lat_lng := _coords(p.get("coords"))):
            meta["latitude"], meta["longitude"] = lat_lng
            # У Гранели есть ЖК в Уфе (forest-symphony) — без явного
            # city базовый код инициализировал бы его как 'msk' и считал
            # distance до Кремля как 1100+ км. Определяем city по
            # ближайшему центру в CITY_CENTERS — если дальше 80 км от
            # любого, оставляем None (build_rows подставит 'msk').
            closest = min(CITY_CENTERS.items(),
                          key=lambda kv: haversine_km(lat_lng[0], lat_lng[1],
                                                       kv[1][0], kv[1][1]))
            if haversine_km(lat_lng[0], lat_lng[1],
                            closest[1][0], closest[1][1]) < 80:
                meta["city"] = closest[0]
        # transport_access_point.transport_point.coords — координаты МЕТРО,
        # а transport_access_point.transport_point.name — название.
        tap = p.get("transport_access_point") or {}
        tp = tap.get("transport_point") or {}
        if tp.get("name"):
            meta["metro_name"] = tp["name"]
            tm = _to_int(tap.get("time"))
            if tm: meta["metro_time_foot"] = tm
        out[slug] = meta
    return out


def collect(*, session: requests.Session | None = None) -> CollectResult:
    """Постранично обходит весь каталог квартир Гранели."""
    s = session or make_session()
    norm_flats: list[NormFlat] = []
    blocks: dict[str, str] = {}              # slug → name
    block_floors: dict[str, int] = {}        # slug → max(floor_count)

    url: str | None = _FLATS_URL
    params: dict | None = {"limit": _PAGE_LIMIT, "offset": 0}
    skipped = 0
    total: int | None = None
    items_seen = 0
    for _ in range(_MAX_PAGES):
        assert url is not None  # break ниже не пускает None в новую итерацию
        payload = request_json(s, "GET", url, params=params)
        params = None  # `next` уже содержит limit/offset
        if total is None:
            total = payload.get("count")
        items_seen += len(payload.get("results") or [])
        for fl in payload.get("results") or []:
            slug = fl.get("project_slug") or fl.get("project")
            if not fl.get("id") or not slug:
                continue
            blocks.setdefault(slug, fl.get("project_full_name")
                                    or fl.get("project") or slug)
            fc = _to_int(fl.get("floor_count"))
            if fc: block_floors[slug] = max(block_floors.get(slug, 0), fc)
            norm_flats.append(_to_norm(fl))
        nxt = payload.get("next")
        if not nxt:
            break
        # SSRF guard через общий helper. Если хост чужой — прерываем обход
        # (не складываем чужой payload как «квартиры Гранели»).
        url = safe_next_url(nxt, "granelle.ru")
        if not url:
            log.warning("Гранель: подозрительный next %r, прерываю обход", nxt)
            skipped += 1
            break
    else:
        log.warning("Гранель: достигнут предел в %d страниц — хвост потерян", _MAX_PAGES)
        skipped += 1
    if not skipped and totals_deficit(total, items_seen):
        # not skipped: обрыв по MAX_PAGES/подозрительному next уже посчитан,
        # дефицит — следствие того же события, не считаем его дважды
        log.warning("Гранель: собрано %d из %s заявленных — недобор", items_seen, total)
        skipped += 1

    project_meta = _fetch_project_meta(s)
    norm_blocks = [
        NormBlock(
            native_id=slug, name=name, slug=slug,
            meta={"floors_max": block_floors.get(slug), **project_meta.get(slug, {})},
        )
        for slug, name in blocks.items()
    ]
    log.info("Гранель: %d ЖК, %d квартир", len(norm_blocks), len(norm_flats))
    return CollectResult(blocks=norm_blocks, flats=norm_flats, skipped=skipped)
