// Общие security-критичные хелперы для index.html и flat.html.
// ОДНА реализация на обе страницы: раньше esc/safeUrl были скопированы и
// уже разъехались (index требовал абсолютный http(s)-URL, flat принимал
// относительные) — одно и то же поле `flats.url` проходило разные фильтры
// в зависимости от страницы.
(function () {
  "use strict";

  // Экранирование для innerHTML и значений атрибутов.
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // Только абсолютные http(s)-URL — строже из двух прежних вариантов:
  // блокирует javascript:/data: и протокол-относительные //evil.com;
  // относительные пути источники не отдают, терять нечего.
  function safeUrl(s) {
    if (s == null) return "";
    const u = String(s).trim();
    return /^https?:\/\//i.test(u) ? u : "";
  }

  function fmtRub(v) {
    if (v == null || v === "") return "—";
    const n = Number(v);
    return Number.isFinite(n) ? n.toLocaleString("ru-RU") + " ₽" : "—";
  }

  window.KV = { escapeHtml, safeUrl, fmtRub };
})();
