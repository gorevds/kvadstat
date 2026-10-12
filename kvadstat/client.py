"""Тонкий клиент к публичному PIK JSON API.

С ~2026-10-10 старый листинг `GET /v2/flat?block_id=…&page=…` сломан на
стороне PIK: почти на все блоки отвечает HTTP 500 `FATAL_ERROR`, на часть —
200 с голым `[]` вместо объекта `{block, flats}`. Сайт pik.ru теперь грузит
квартиры через `GET /v2/filter?block=…&onlyFlats=1&flatPage=…&flatLimit=…`:
ответ `{count, blocks: [{…block-поля…, flats: […]}]}`, страница ≤ 50 квартир
(больший flatLimit сервер молча режет), за концом — `blocks: []`.
Формат flat-item в filter беднее (нет url/pdf/updatedAt/areaKitchen, ключи
camelCase) — нормализация в kvadstat.sources.pik._norm_flat.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable

import requests

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
BASE_URL = "https://api.pik.ru/v2/filter"
# Сервер режет flatLimit до 50 — больше просить бессмысленно
PAGE_SIZE = 50
MAX_PAGES = 200

# Тяжёлые block-поля filter-ответа (HTML-описание, картинки, ~40 КБ на блок):
# не копируем их в каждый flat-item — 10k квартир × 40 КБ = лишние сотни МБ.
_BLOCK_HEAVY_KEYS = frozenset({
    "flats", "description", "gradify", "image", "attributesData",
    "titleIntroSlider", "namePrepositional",
})

# 500 — тоже ретраим: /v2/filter под нагрузкой эпизодически отвечает
# 500 FATAL_ERROR на середине пагинации блока, повтор через 1-5 с проходит;
# без ретрая одна такая страница выбрасывала весь блок в skipped.
_RETRYABLE_STATUS = (429, 500, 502, 503, 504)


class PikApiError(RuntimeError):
    pass


def _default_backoff(attempt: int) -> float:
    return [1, 5, 15, 45][min(attempt, 3)]


class PikClient:
    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_UA,
        retries: int = 3,
        backoff: Callable[[int], float] = _default_backoff,
        timeout: float = 20.0,
        session: requests.Session | None = None,
    ):
        self.retries = retries
        self.backoff = backoff
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": "https://www.pik.ru/",
        })

    def fetch_block_flats(
        self, *, block_id: int, types: Iterable[int] = (1,)
    ) -> list[dict]:
        items: list[dict] = []
        seen: set = set()
        page = 1
        while True:
            payload = self._get_page(block_id=block_id, types=types, page=page)
            flats = _extract_flats(payload, block_id=block_id, page=page)
            if not flats:
                break
            fresh = [f for f in flats if f.get("id") not in seen]
            if not fresh:
                # сервер игнорирует flatPage и крутит одну страницу —
                # дальше только дубли, а не «pagination runaway» на 200 стр.
                break
            seen.update(f.get("id") for f in fresh)
            items.extend(fresh)
            page += 1
            if page > MAX_PAGES:
                raise PikApiError(f"pagination runaway at page {page}")
        return items

    def _get_page(self, *, block_id: int, types: Iterable[int], page: int) -> dict:
        params = {
            "block": block_id,
            "types": ",".join(str(t) for t in types),
            "onlyFlats": 1,
            "flatLimit": PAGE_SIZE,
            "flatPage": page,
        }  # type: dict[str, str | int]
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.session.get(BASE_URL, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_exc = exc
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        last_exc = PikApiError(f"non-JSON body: {exc}")
                elif resp.status_code in _RETRYABLE_STATUS:
                    last_exc = PikApiError(f"HTTP {resp.status_code}")
                else:
                    raise PikApiError(
                        f"HTTP {resp.status_code} for page={page}: "
                        f"{resp.text[:200]}"
                    )
            if attempt < self.retries:
                time.sleep(self.backoff(attempt))
        raise PikApiError(
            f"page {page} failed after {self.retries + 1} attempts: {last_exc}"
        )


def _extract_flats(payload, *, block_id: int, page: int) -> list[dict]:
    """Ответ PIK → flat-items одной страницы (пустой список = конец).

    Понимает оба формата:
      * /v2/filter: `{blocks: [{id, …, flats: [...]}]}` — block-поля (метро,
        координаты, slug) кладём в `item["block"]`, как было в /v2/flat, чтобы
        _norm_block / geo.extract_block_meta работали без изменений;
      * legacy /v2/flat: `{block: {…}, flats: [...]}`.
    Любая другая форма (голый список, строка) — PikApiError, а не
    AttributeError посреди сбора: блок уйдёт в failed/skipped.
    """
    if not isinstance(payload, dict):
        raise PikApiError(
            f"page {page}: unexpected payload type {type(payload).__name__}: "
            f"{str(payload)[:100]}"
        )
    if "blocks" in payload:
        blocks = payload.get("blocks") or []
        if not isinstance(blocks, list):
            raise PikApiError(f"page {page}: 'blocks' is {type(blocks).__name__}")
        out: list[dict] = []
        for b in blocks:
            if not isinstance(b, dict) or b.get("id") != block_id:
                continue
            block_info = {k: v for k, v in b.items() if k not in _BLOCK_HEAVY_KEYS}
            for fl in b.get("flats") or []:
                if not isinstance(fl, dict):
                    continue
                own = fl.get("block")
                fl["block"] = {**block_info, **(own if isinstance(own, dict) else {})}
                out.append(fl)
        return out
    flats = payload.get("flats") or []
    if not isinstance(flats, list):
        raise PikApiError(f"page {page}: 'flats' is {type(flats).__name__}")
    return [f for f in flats if isinstance(f, dict)]
