import responses

from kvadstat.client import PikApiError, PikClient

BLOCK_URL = "https://api.pik.ru/v2/filter"


def _page(page: int, count: int, n: int):
    """/v2/filter-форма: блок с вложенными flats; за концом — blocks: []."""
    if n == 0:
        return {"count": 0, "countBlocks": 0, "blocks": [], "lastBlock": 0}
    return {"count": count, "countBlocks": 1, "lastBlock": 1165, "blocks": [{
        "id": 1165, "name": "Нарвин", "url": "narvin", "latitude": 55.84,
        "description": "<div>тяжёлое HTML</div>",
        "flats": [
            {
                "id": page * 100 + i,
                "guid": f"g-{page}-{i}",
                "block_id": 1165,
                "bulk_id": 10395,
                "rooms": 1,
                "floor": 1,
                "area": 30.0,
                "price": 1000,
                "meterPrice": 100,
                "status": "free",
                "name": "x",
                "url": "x",
                "updatedAt": "x",
            }
            for i in range(n)
        ],
    }]}


@responses.activate
def test_fetch_paginates_until_empty():
    responses.add(responses.GET, BLOCK_URL, json=_page(1, 75, 50), status=200)
    responses.add(responses.GET, BLOCK_URL, json=_page(2, 75, 25), status=200)
    responses.add(responses.GET, BLOCK_URL, json=_page(3, 75, 0), status=200)

    client = PikClient(retries=0, backoff=lambda _i: 0)
    items = client.fetch_block_flats(block_id=1165, types=(1,))

    assert len(items) == 75
    assert items[0]["id"] == 100
    assert items[-1]["id"] == 224


@responses.activate
def test_fetch_retries_on_502():
    responses.add(responses.GET, BLOCK_URL, status=502)
    responses.add(responses.GET, BLOCK_URL, status=502)
    responses.add(responses.GET, BLOCK_URL, json=_page(1, 1, 1), status=200)
    responses.add(responses.GET, BLOCK_URL, json=_page(2, 1, 0), status=200)

    client = PikClient(retries=2, backoff=lambda _i: 0)
    items = client.fetch_block_flats(block_id=1165, types=(1,))

    assert len(items) == 1


@responses.activate
def test_fetch_gives_up_after_retries():
    for _ in range(4):
        responses.add(responses.GET, BLOCK_URL, status=502)

    client = PikClient(retries=2, backoff=lambda _i: 0)
    try:
        client.fetch_block_flats(block_id=1165, types=(1,))
    except PikApiError:
        return
    raise AssertionError("PikApiError not raised")


@responses.activate
def test_fetch_sends_browser_user_agent():
    responses.add(responses.GET, BLOCK_URL, json=_page(1, 0, 0), status=200)
    client = PikClient(retries=0, backoff=lambda _i: 0)
    client.fetch_block_flats(block_id=1165, types=(1,))
    ua = responses.calls[0].request.headers["User-Agent"]
    assert "Mozilla" in ua and "AppleWebKit" in ua


@responses.activate
def test_fetch_attaches_block_info_without_heavy_fields():
    responses.add(responses.GET, BLOCK_URL, json=_page(1, 1, 1), status=200)
    responses.add(responses.GET, BLOCK_URL, json=_page(2, 1, 0), status=200)
    items = PikClient(retries=0, backoff=lambda _i: 0).fetch_block_flats(block_id=1165)
    assert items[0]["block"]["url"] == "narvin"
    assert items[0]["block"]["latitude"] == 55.84
    assert "description" not in items[0]["block"]
    assert "flats" not in items[0]["block"]
    q = responses.calls[0].request.url
    assert "block=1165" in q and "flatPage=1" in q and "onlyFlats=1" in q


@responses.activate
def test_fetch_ignores_other_blocks_in_response():
    page = _page(1, 1, 1)
    page["blocks"].append({"id": 999, "flats": [{"id": 1}]})
    responses.add(responses.GET, BLOCK_URL, json=page, status=200)
    responses.add(responses.GET, BLOCK_URL, json=_page(2, 1, 0), status=200)
    items = PikClient(retries=0, backoff=lambda _i: 0).fetch_block_flats(block_id=1165)
    assert [it["id"] for it in items] == [100]


@responses.activate
def test_fetch_stops_when_server_repeats_same_page():
    # сервер игнорирует flatPage — без стопа по «нет новых id» был бы
    # pagination runaway на 200 страниц дублей
    for _ in range(3):
        responses.add(responses.GET, BLOCK_URL, json=_page(1, 50, 50), status=200)
    items = PikClient(retries=0, backoff=lambda _i: 0).fetch_block_flats(block_id=1165)
    assert len(items) == 50
    assert len(responses.calls) == 2


@responses.activate
def test_fetch_bare_list_payload_is_api_error_not_crash():
    # 2026-10: /v2/flat начал отвечать 200 `[]` — раньше это давало
    # AttributeError: 'list' object has no attribute 'get' и валило весь ПИК
    responses.add(responses.GET, BLOCK_URL, json=[], status=200)
    client = PikClient(retries=0, backoff=lambda _i: 0)
    try:
        client.fetch_block_flats(block_id=1165)
    except PikApiError:
        return
    raise AssertionError("PikApiError not raised")


@responses.activate
def test_fetch_still_understands_legacy_flat_payload():
    legacy = {"block": {"id": 1165}, "flats": [{"id": 1, "block_id": 1165}]}
    responses.add(responses.GET, BLOCK_URL, json=legacy, status=200)
    responses.add(responses.GET, BLOCK_URL, json={"block": {}, "flats": []}, status=200)
    items = PikClient(retries=0, backoff=lambda _i: 0).fetch_block_flats(block_id=1165)
    assert [it["id"] for it in items] == [1]


@responses.activate
def test_fetch_retries_on_transient_500():
    responses.add(responses.GET, BLOCK_URL, status=500,
                  json={"success": False, "data": {"error": "FATAL_ERROR"}})
    responses.add(responses.GET, BLOCK_URL, json=_page(1, 1, 1), status=200)
    responses.add(responses.GET, BLOCK_URL, json=_page(2, 1, 0), status=200)
    items = PikClient(retries=2, backoff=lambda _i: 0).fetch_block_flats(block_id=1165)
    assert len(items) == 1
