/* 落地页脚本：统计数字从监控台 API 取真实值（同源单端口），easeOutCubic 滚动计数。 */
(function () {
  "use strict";

  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function fmt(value, decimals) {
    return Number(value).toFixed(decimals);
  }

  function countUp(el, target, decimals, duration) {
    // 后台标签页 rAF 会被暂停导致计数永不执行：用 setInterval 驱动（前后台都可靠）
    if (reduced || document.hidden) {
      el.textContent = fmt(target, decimals);
      return;
    }
    var start = null;
    var timer = setInterval(function () {
      var now = performance.now();
      if (start === null) start = now;
      var t = Math.min(1, (now - start) / duration);
      var eased = 1 - Math.pow(1 - t, 3);
      el.textContent = fmt(target * eased, decimals);
      if (t >= 1) clearInterval(timer);
    }, 16);
  }

  var stats = [
    { id: "st-creators", decimals: 0 },
    { id: "st-live", decimals: 0 },
    { id: "st-items", decimals: 0 },
    { id: "st-segments", decimals: 1 },
  ];

  function startCounters(values) {
    window.__radar.counted = JSON.stringify(values);
    stats.forEach(function (s, i) {
      var el = document.getElementById(s.id);
      if (!el) return;
      var v = values[i] != null ? values[i] : 0;
      var duration = 1500 + i * 80;
      setTimeout(function () {
        countUp(el, v, s.decimals, duration);
      }, 480 + i * 90);
    });
  }

  function fetchRealStats() {
    // 单端口同源：直接取监控台 API 的真实数据；失败则全 0 兜底
    window.__radar.fetched = true;
    return fetch("/api/v1/live/monitors")
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (monitors) {
        var segments = monitors.reduce(function (acc, m) { return acc + (m.transcript_count || 0); }, 0);
        var live = monitors.filter(function (m) { return m.is_live === true; }).length;
        var values = [monitors.length, live, null, Math.round(segments) / 1000];
        return fetch("/api/v1/source-items?status=transcribed&limit=1")
          .then(function (r) { return r.ok ? r.json() : []; })
          .then(function (rows) {
            // total 不在响应里：用 limit=500 近似计数
            return fetch("/api/v1/source-items?status=transcribed&limit=500")
              .then(function (r2) { return r2.ok ? r2.json() : []; })
              .then(function (all) {
                values[2] = all.length;
                return values;
              });
          });
      })
      .catch(function () { return [0, 0, 0, 0]; });
  }

  function boot() {
    window.__radar = { boot: true, fetched: false, counted: false };
    // 单屏落地页，统计条始终可见：直接加载即取数，不依赖 IO/rAF（后台标签页会被冻结）
    fetchRealStats().then(startCounters).catch(function () {
      startCounters([0, 0, 0, 0]);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
