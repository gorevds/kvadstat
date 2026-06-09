# kvadstat

Ежедневный снимок цен и параметров квартир застройщиков Москвы → SQLite
→ публичная фильтруемая витрина через Datasette.

Изначально — только ПИК (по умолчанию **ЖК «Нарвин»**, `block_id=1165`),
теперь покрывает 10 застройщиков: ПИК, ГК ФСК, Донстрой, А101, Level,
Абсолют, MR Group, Гранель, Инград, Брусника. Все они лежат в одной БД с
разрезом по застройщику; ПИК — такой же источник, как остальные. Самолёт и ЛСР зарезервированы в реестре
id, но модулей-источников пока нет (причины ниже).

Живая витрина: **https://kvadstat.gorev.space**

## Что внутри

- `kvadstat/client.py` — пагинированный клиент к `api.pik.ru/v2/flat` с retry на 502/503/504
- `kvadstat/sources/` — по модулю на застройщика (10 шт.: pik, fsk, donstroy, a101,
  level, absolut, mrgroup, granel, ingrad, brusnika), каждый приводит свой
  API/HTML к нормализованным `NormBlock`/`NormFlat`; `base.py` единообразно
  собирает из них строки `blocks`/`flats`/`snapshots` (`build_rows`)
- `kvadstat/mapping.py` — legacy per-flat маппинг ПИК; используется backfill'ом.
  Живой скан ПИК идёт через `kvadstat/sources/pik.py` (единый контракт со всеми)
- `kvadstat/store.py` — схема, идемпотентный upsert и материализация view'ов
- `kvadstat/developers.py` — реестр застройщиков + неймспейсинг id (общее id-пространство)
- `kvadstat/backfill_wayback.py` — ретро-история из архивов pik.ru через web.archive.org
- `bin/scan_dev.py` — **единая точка входа**: параллельный сканер всех 10
  застройщиков (`--all` или `--developer NAME`)
- `bin/scan.py` — тонкий deprecation-шим: делегирует в `scan_dev --developer "ПИК"`
- `bin/backfill.py` — одноразовая заливка истории
- `metadata.yml` — конфиг Datasette
- `deploy/` — systemd units + nginx + install.sh

### Застройщики кроме ПИК

```bash
python -m bin.scan_dev --db data/kvadstat.db --all          # все 10 источников параллельно
python -m bin.scan_dev --db data/kvadstat.db --developer "ГК ФСК"
python -m bin.scan_dev --db data/kvadstat.db --developer "ПИК"   # ПИК — обычный источник
```

Самолёт и ЛСР пока не поддержаны: сайт Самолёта за QRATOR-JS-челленджем
(нужен headless-браузер), сайт ЛСР отдаёт данные только с российских IP.
Offset'ы под них в `kvadstat/developers.py` уже зарезервированы.

## Локальный quick start

```bash
python3.12 -m venv venv
. venv/bin/activate
pip install -e .[serve,test]
pytest                                       # 237 тестов
python -m bin.scan_dev --db data/kvadstat.db --developer "ПИК"   # ~1-2 мин, реальный API
datasette serve data/kvadstat.db -m metadata.yml --port 5051
```

`http://127.0.0.1:5051/kvadstat/today_one_room` — сегодняшняя витрина (1-комн).

### Ретро-история из Wayback Machine

```bash
python -m bin.backfill --db data/kvadstat.db --slug narvin --block-id 1165
```

Для Нарвина даёт ~9 исторических срезов с июня 2025 (~160 уникальных квартир,
рост цен 1к ~+28% Jun 2025 → Jan 2026).

## Другой ЖК ПИК

Нужны две вещи: **PIK block_id** и **URL slug** проекта. Найти их можно
через один любой `flat/{id}` выбранного дома:

```bash
curl -A "Mozilla/5.0" "https://api.pik.ru/v1/flat/<flat-id>" | jq '{block_id, url}'
```

Залейте ретро-историю (она же регистрирует блок в `blocks`):

```bash
python -m bin.backfill --db data/kvadstat.db --slug <slug> --block-id <BID>
```

Дальше ежедневный `scan_dev --developer "ПИК"` сам подхватит блок: список
PIK-блоков берётся из таблицы `blocks` (где `developer='ПИК'`), отдельный
`--block-id` не нужен. `bin/scan --block-id …` ещё работает как
deprecation-шим, но новый код должен звать `bin.scan_dev`.

БД содержит `flats.block_id` — все ЖК всех застройщиков живут в одной таблице.

## На сервере

DNS A-запись для домена → IP сервера должна быть. Дальше:

```bash
sudo REPO_DIR=$PWD bash deploy/install.sh
sudo certbot --nginx -d <domain>
sudo nginx -t && sudo systemctl reload nginx
```

Скан ежедневно в 06:00 МСК через `kvadstat-scan.timer`. Состояние:

```bash
systemctl status kvadstat.service kvadstat-scan.timer
systemctl list-timers kvadstat-scan.timer
journalctl -u kvadstat-scan.service -n 50
```

## Источник данных

```
GET https://api.pik.ru/v2/flat?block_id=<BID>&types=1&page=N
```

50 элементов на страницу, требует браузерный User-Agent (его выставляет
`PikClient`). Сайт `pik.ru` под QRATOR — публичный сайт не парсим, JSON-API
этого не требует.

## Схема БД

- `flats` — стабильные характеристики (этаж, площадь, корпус, секция); один ряд на квартиру
- `snapshots` — ежедневный срез (цена, статус, отделка, лучшая ипотека); PK = (flat_id, scan_date)
- `blocks` — ЖК + гео-мета (метро, координаты, город, застройщик)
- `today_all` / `today_one_room` / `flat_sparkline_30d` — **материализованные**
  таблицы (пересоздаются в конце скана через `refresh_materialized`), с
  русскими колонками; на них стоит витрина
- `scan_runs` — журнал сканов (статус `ok`/`partial`/`error`, счётчики, длительность)
- Все ЖК всех застройщиков в одной БД, фильтруйте по `застройщик` / `flats.block_id`

## Лицензия

Apache-2.0 (см. `LICENSE`).
