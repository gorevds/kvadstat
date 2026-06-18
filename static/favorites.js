// Избранное (per-browser): множество id квартир в localStorage.
// Без бэкенда/авторизации — приватно для браузера, между устройствами не
// синкается. Грузится после common.js (нужен window.KV) и до инлайн-кода
// страниц, поэтому KV.fav гарантированно есть к моменту использования.
(function () {
  "use strict";
  var KEY = "kvadstat:favorites:v1";
  var listeners = [];

  // Любой сбой чтения (нет localStorage, битый JSON, не массив) → пустой
  // список: избранное не критично, страница падать из-за него не должна.
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
  function emit() {
    listeners.forEach(function (cb) { try { cb(); } catch (_) {} });
  }

  function list() { return read(); }
  function count() { return read().length; }
  function has(id) { id = Number(id); return read().indexOf(id) !== -1; }
  function add(id) {
    id = Number(id);
    if (!Number.isFinite(id)) return;
    var ids = read();
    if (ids.indexOf(id) === -1) { ids.push(id); write(ids); emit(); }
  }
  function remove(id) {
    id = Number(id);
    var ids = read().filter(function (x) { return x !== id; });
    write(ids); emit();
  }
  function toggle(id) {
    id = Number(id);
    if (!Number.isFinite(id)) return false;
    if (has(id)) { remove(id); return false; }
    add(id); return true;
  }
  function onChange(cb) { if (typeof cb === "function") listeners.push(cb); }

  // Изменение из другой вкладки браузера — синхронизируем UI.
  window.addEventListener("storage", function (e) {
    if (e.key === KEY) emit();
  });

  window.KV = window.KV || {};
  window.KV.fav = {
    has: has, toggle: toggle, add: add, remove: remove,
    list: list, count: count, onChange: onChange,
  };
})();
