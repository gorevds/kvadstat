# Favorites Implementation Plan

> **For agentic workers:** implement task-by-task; run gates after each; commit per task.

**Goal:** Let users star flats, see them in a dedicated ⭐ tab, and softly highlight them in the list.

**Architecture:** Per-browser localStorage set of flat ids (`KV.fav`); a toggle button in the detail panel; a soft `is-fav` row class shared by both Tabulator tables; a 4th tab with its own Tabulator showing active favorites (from in-memory today_all) plus, on demand, sold-out ones fetched via a `fav_flats` canned query (today_all column shape, latest snapshot per id).

**Tech Stack:** Vanilla JS SPA (static/index.html, static/common.js), Datasette canned query (metadata.yml), pytest, playwright/chromium.

## Global Constraints

- Respond in Russian; code/commits/PR in English.
- Commit as `git -c user.name="Dmitry Gorev" -c user.email="dmitrii@gorev.space"`; no Claude co-author.
- One logical change per commit.
- `oscar@dolotov.com` must not appear in code.
- All user-facing HTML values escaped (XSS): use `KV.escapeHtml`, `KV.safeUrl`.
- `default_allow_sql off` — only canned queries, no arbitrary `?sql=`.

---

### Task 1: `fav_flats` canned query (backend)

**Files:**
- Modify: `metadata.yml` (under `databases.kvadstat.queries`)
- Test: `tests/test_fav_flats_query.py` (create)

**Interfaces:**
- Produces: GET `/kvadstat/fav_flats.json?ids=<json-array>&_shape=array` → rows with
  today_all column names (`id, застройщик, жк, …, дата_среза, block_id`), one per
  requested id at its latest snapshot.

- [ ] **Step 1: failing test** — build a tiny in-memory/temp DB with the project schema,
  insert 2 flats (one sold-out: its latest snapshot older than another flat's), run the
  `fav_flats` SQL with `:ids='[id1,id2]'`, assert: 2 rows, Russian column `жк` present,
  each row is the flat's latest snapshot price.
- [ ] **Step 2: run, expect FAIL** (`fav_flats` SQL not yet in metadata / helper reads it).
- [ ] **Step 3: add the query to metadata.yml**:

```yaml
      fav_flats:
        title: Избранные квартиры — последний известный срез
        sql: |-
          WITH want AS (SELECT value AS id FROM json_each(:ids)),
          latest AS (
              SELECT s.flat_id AS flat_id, MAX(s.scan_date) AS scan_date
              FROM snapshots s JOIN want w ON w.id = s.flat_id
              GROUP BY s.flat_id
          )
          SELECT
              f.id AS id,
              b.developer AS застройщик,
              COALESCE(b.name, 'block ' || f.block_id) AS жк,
              COALESCE(b.city, 'msk') AS город,
              COALESCE(b.klass, 'н/д') AS класс,
              b.metro_name AS метро,
              CASE b.metro_line_type WHEN 1 THEN 'M' WHEN 2 THEN 'МЦК'
                   WHEN 3 THEN 'МЦД' WHEN 4 THEN 'электр.' ELSE NULL END AS тип_транспорта,
              b.metro_time_foot AS "мин_пешком",
              b.metro_line_name AS линия,
              b.distance_km AS "км_от_центра",
              CASE f.rooms WHEN 'studio' THEN 'студия' WHEN '-1' THEN 'студия'
                   ELSE f.rooms || 'к' END AS комнат,
              f.is_apartment AS апартаменты,
              f.bulk_name AS корпус,
              f.section_no AS секция,
              f.floor AS этаж,
              f.area AS "площадь_м²",
              COALESCE(s.old_price, s.price) AS базовая_цена,
              s.promo_price AS "цена_по_программе",
              s.base_meter_price AS "база_за_м²",
              s.meter_price AS "по_программе_за_м²",
              s.has_promo AS промо,
              s.discount_pct AS "скидка_%",
              s.mortgage_best_name AS программа,
              s.status AS статус,
              s.finish AS отделка,
              f.settlement_date AS заселение,
              b.floors_max AS "этажей_всего",
              b.address AS адрес,
              f.name AS артикул,
              f.url AS ссылка,
              f.plan_url AS планировка,
              s.scan_date AS дата_среза,
              f.block_id AS block_id
          FROM flats f
          JOIN latest l ON l.flat_id = f.id
          JOIN snapshots s ON s.flat_id = f.id AND s.scan_date = l.scan_date
          LEFT JOIN blocks b ON b.id = f.block_id
```

  The test reads the SQL out of `metadata.yml` (yaml.safe_load) and executes it against the
  temp DB with a `json_each` bound param, so it stays in sync with the deployed query.
- [ ] **Step 4: run, expect PASS.**
- [ ] **Step 5: commit** `feat: add fav_flats canned query for sold-out favorites`.

---

### Task 2: `KV.fav` localStorage module

**Files:**
- Create: `static/favorites.js`
- Modify: `static/index.html` (`<head>`, after common.js), `static/flat.html` (`<head>`,
  after common.js) — load with `defer`. (flat.html loads it for API symmetry/future use;
  no UI added there now.)

**Interfaces:**
- Produces: `window.KV.fav = { has(id)->bool, toggle(id)->bool, add(id), remove(id),
  list()->number[], count()->number, onChange(cb) }`. Key `kvadstat:favorites:v1`.

- [ ] **Step 1:** write `static/favorites.js`:

```js
// Избранное (per-browser): множество id квартир в localStorage.
// Без бэкенда/авторизации — приватно для браузера, между устройствами не синкается.
(function () {
  "use strict";
  var KEY = "kvadstat:favorites:v1";
  var listeners = [];

  function read() {
    try {
      var raw = localStorage.getItem(KEY);
      if (!raw) return [];
      var arr = JSON.parse(raw);
      if (!Array.isArray(arr)) return [];
      return arr.map(Number).filter(function (n) { return Number.isFinite(n); });
    } catch (_) { return []; }
  }
  function write(ids) {
    try { localStorage.setItem(KEY, JSON.stringify(ids)); } catch (_) {}
  }
  function emit() { listeners.forEach(function (cb) { try { cb(); } catch (_) {} }); }

  function list() { return read(); }
  function count() { return read().length; }
  function has(id) { id = Number(id); return read().indexOf(id) !== -1; }
  function add(id) {
    id = Number(id); if (!Number.isFinite(id)) return;
    var ids = read(); if (ids.indexOf(id) === -1) { ids.push(id); write(ids); emit(); }
  }
  function remove(id) {
    id = Number(id);
    var ids = read().filter(function (x) { return x !== id; });
    write(ids); emit();
  }
  function toggle(id) {
    id = Number(id); if (!Number.isFinite(id)) return false;
    if (has(id)) { remove(id); return false; }
    add(id); return true;
  }
  function onChange(cb) { if (typeof cb === "function") listeners.push(cb); }

  // Изменение из другой вкладки браузера.
  window.addEventListener("storage", function (e) {
    if (e.key === KEY) emit();
  });

  window.KV = window.KV || {};
  window.KV.fav = { has: has, toggle: toggle, add: add, remove: remove,
                    list: list, count: count, onChange: onChange };
})();
```

- [ ] **Step 2:** add `<script defer src="/static/favorites.js"></script>` right after the
  common.js script tag in both index.html and flat.html `<head>`.
- [ ] **Step 3: commit** `feat: add KV.fav localStorage favorites store`.

---

### Task 3: favorite toggle button in detail panel

**Files:**
- Modify: `static/index.html` — `.detail-head` markup (~line 847), `openDetail` (~1580),
  add `detailFlatId` state + button wiring, CSS for `.detail-fav`.

**Interfaces:**
- Consumes: `KV.fav`. Produces: `refreshFavUI()` (Task 4 fills row/counter parts; here it
  at least updates the button + tab counter — define it now, extend in Task 4/5).

- [ ] **Step 1:** in `.detail-head`, before `.detail-close`, add:

```html
<button class="detail-fav" id="detailFavBtn" type="button" aria-pressed="false">☆ В избранное</button>
```

- [ ] **Step 2:** add module-scope `let detailFlatId = null;` near `detailReturnFocus`.
- [ ] **Step 3:** in `openDetail`, after computing title, set
  `detailFlatId = Number(row.id);` and `syncFavBtn();`.
- [ ] **Step 4:** add helpers:

```js
function syncFavBtn() {
  const btn = document.getElementById("detailFavBtn");
  if (!btn) return;
  const on = detailFlatId != null && KV.fav.has(detailFlatId);
  btn.classList.toggle("on", on);
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.textContent = on ? "★ В избранном" : "☆ В избранное";
}
```

- [ ] **Step 5:** wire click once in init (near other detail listeners):

```js
document.getElementById("detailFavBtn").addEventListener("click", () => {
  if (detailFlatId == null) return;
  KV.fav.toggle(detailFlatId);
  refreshFavUI();
});
```

- [ ] **Step 6:** add CSS:

```css
.detail-fav { border:1px solid var(--border); background:#fff; color:var(--accent);
  border-radius:6px; padding:4px 10px; font-size:13px; cursor:pointer; margin-right:8px; }
.detail-fav.on { background:#fff7e6; border-color:#f0c674; color:#b7791f; }
```

- [ ] **Step 7:** add a minimal `refreshFavUI()` (extended later):

```js
function refreshFavUI() { syncFavBtn(); updateFavCount(); reformatFavRow(); renderFavoritesTabIfActive(); }
```

  Stub the not-yet-defined helpers as no-ops now if needed, or define them in Tasks 4/5
  before this calls them — implement Tasks 4 & 5 in the same branch so all exist.
- [ ] **Step 8: commit** `feat: favorite toggle button in flat detail panel`.

---

### Task 4: soft highlight in objects table + per-row refresh

**Files:**
- Modify: `static/index.html` — `rowFormatter` (~995), CSS, `refreshFavUI` helpers.

- [ ] **Step 1:** extend `rowFormatter`:

```js
const rowFormatter = (row) => {
  const d = row.getData();
  if (d["промо"] === 1) row.getElement().classList.add("promo-row");
  if (KV.fav.has(d.id)) row.getElement().classList.add("is-fav");
  else row.getElement().classList.remove("is-fav");
  if (d._gone) row.getElement().classList.add("is-gone");
};
```

- [ ] **Step 2:** CSS — soft, neutral (no loud fill):

```css
.tabulator-row.is-fav { background: #fffbeb; }
.tabulator-row.is-fav:hover { background: #fff5d6; }
.tabulator-row.is-gone { opacity: .55; }
```

- [ ] **Step 3:** implement row/counter helpers used by `refreshFavUI`:

```js
function reformatFavRow() {
  // detailFlatId — единственная строка, чьё состояние только что менялось
  [table, favTable].forEach(t => {
    if (!t) return;
    try { const r = t.getRow(detailFlatId); if (r) r.reformat(); } catch (_) {}
  });
}
function updateFavCount() {
  const el = document.getElementById("favCount");
  if (el) el.textContent = KV.fav.count();
}
```

- [ ] **Step 4: commit** `feat: soft-highlight favorited rows in objects table`.

---

### Task 5: ⭐ Favorites tab (favTable + show-gone toggle)

**Files:**
- Modify: `static/index.html` — `nav.tabs` (~666), new `#tab-favorites` section,
  `switchTab`/`applyHash` allow-list, `favTable` lifecycle, fetch sold-out.

**Interfaces:**
- Consumes: `allRows`, `columns`, `rowFormatter`, `openDetail`, `KV.fav`,
  `/kvadstat/fav_flats.json`. Produces: `renderFavorites()`,
  `renderFavoritesTabIfActive()`.

- [ ] **Step 1:** add tab button after "objects":

```html
<button type="button" data-tab="favorites" role="tab" aria-selected="false"
        aria-controls="tab-favorites">⭐ Избранное <span id="favCount">0</span></button>
```

- [ ] **Step 2:** add section before `</main>`:

```html
<section id="tab-favorites" class="tab-panel">
  <div class="controls controls-sticky">
    <span id="favSummary" style="color:var(--muted);font-size:13px"></span>
    <label style="margin-left:auto"><input type="checkbox" id="favShowGone"> Показать снятые с продажи</label>
  </div>
  <div id="favEmpty" class="fav-empty" hidden>Пока ничего не добавлено. Откройте квартиру и нажмите ★.</div>
  <div id="favGrid"></div>
</section>
```

- [ ] **Step 3:** module state `let favTable = null; let favGoneCache = null;`.
- [ ] **Step 4:** `renderFavorites()`:

```js
async function renderFavorites() {
  const favIds = KV.fav.list();
  const empty = document.getElementById("favEmpty");
  const grid = document.getElementById("favGrid");
  const summary = document.getElementById("favSummary");
  if (!favIds.length) {
    if (favTable) favTable.replaceData([]);
    empty.hidden = false; grid.style.display = "none";
    summary.textContent = ""; return;
  }
  empty.hidden = true; grid.style.display = "";
  const favSet = new Set(favIds);
  const active = allRows.filter(r => favSet.has(Number(r.id)));
  const activeIds = new Set(active.map(r => Number(r.id)));
  let rows = active.slice();
  const showGone = document.getElementById("favShowGone").checked;
  const missing = favIds.filter(id => !activeIds.has(id));
  if (showGone && missing.length) {
    try {
      const url = `/kvadstat/fav_flats.json?ids=${encodeURIComponent(JSON.stringify(missing))}&_shape=array`;
      const resp = await fetch(url);
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const gone = (await resp.json()).map(r => Object.assign(r, { _gone: 1 }));
      rows = rows.concat(gone);
    } catch (e) {
      summary.textContent = "не удалось загрузить снятые с продажи";
    }
  }
  if (!favTable) {
    favTable = new Tabulator("#favGrid", {
      data: rows, columns: columns.map(c => ({ ...c })),
      layout: "fitData", height: "calc(100vh - 230px)",
      rowFormatter, initialSort: [{ column: "базовая_цена", dir: "asc" }],
      pagination: false, persistence: false,
    });
    favTable.on("rowClick", (e, row) => {
      if (e.target && e.target.closest("a, button")) return;
      if (window.getSelection && window.getSelection().toString().length) return;
      openDetail(row.getData(), e.target);
    });
  } else {
    favTable.replaceData(rows);
  }
  const goneN = rows.filter(r => r._gone).length;
  summary.textContent = `${rows.length} в избранном` + (goneN ? ` · ${goneN} снято с продажи` : "");
}
function renderFavoritesTabIfActive() {
  const panel = document.getElementById("tab-favorites");
  if (panel && panel.classList.contains("active")) renderFavorites();
}
```

- [ ] **Step 5:** `switchTab`: when `tab === "favorites"` call `renderFavorites()` then
  `setTimeout(() => favTable && favTable.redraw(true), 0)`. Add `"favorites"` to the
  allow-list array in `applyHash` (`["home","zhk","objects","favorites"]`).
- [ ] **Step 6:** `#favShowGone` change listener → `renderFavorites()`.
- [ ] **Step 7:** init: `KV.fav.onChange(refreshFavUI); updateFavCount();`.
- [ ] **Step 8:** CSS `.fav-empty { padding:40px; text-align:center; color:var(--muted); }`.
- [ ] **Step 9: commit** `feat: ⭐ favorites tab with active + sold-out flats`.

---

### Task 6: playwright e2e + gates

**Files:**
- Test: `tests/test_favorites_ui.py` (create, playwright; mirror existing live/ui test style)

- [ ] **Step 1:** e2e against a locally served datasette + static: open page, open a row's
  detail, click `#detailFavBtn`, assert `#favCount` becomes "1", switch to favorites tab,
  assert one row, assert objects-table row has `.is-fav`, reload, assert count persists,
  toggle off, assert removed.
- [ ] **Step 2:** run `pytest tests/test_favorites_ui.py -v`.
- [ ] **Step 3:** gates: `ruff check .`, `mypy kvadstat bin`, `pytest -q` (non-live).
- [ ] **Step 4: commit** `test: e2e favorites flow`.

---

### Task 7: deploy + live verify

- [ ] `DEPLOY_SSH='sshpass -e ssh -o StrictHostKeyChecking=no' bash deploy/deploy.sh root@132.243.235.24`
  (rsyncs metadata.yml + static, restarts datasette → canned query live).
- [ ] live playwright: star a flat on prod, check tab + highlight + sold-out toggle hits
  `fav_flats.json` 200.
- [ ] update memory (`reference_kvadstat_access.md`, open-issues) with the favorites feature.

## Self-Review

- Spec coverage: storage (T2), detail button (T3), highlight (T4), tab + active/sold-out
  (T5), canned query (T1), tests (T6), deploy (T7) — all covered.
- Type consistency: `refreshFavUI` → `syncFavBtn/updateFavCount/reformatFavRow/
  renderFavoritesTabIfActive`; `favTable`, `detailFlatId`, `_gone` used consistently.
- `fav_flats` column names match today_all (so rows reuse `columns` without remap).
- Placeholder scan: none.
