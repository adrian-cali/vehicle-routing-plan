/**
 * VRP Console — Enterprise SPA
 *
 * Multi-page React SPA with sidebar navigation.
 * Pages: Dashboard, Map View, Job Control, Realtime, Route Metrics.
 * Connected to FastAPI backend via REST + WebSocket.
 */
(() => {
  "use strict";

  /* ── Storage versioning — clears stale data on code updates ──────── */
  const APP_VERSION = "FORCE23";
  if (localStorage.getItem("vrp:version") !== APP_VERSION) {
    ["vrp:routes", "vrp:routeSummaries", "vrp:page", "vrp:jobId", "vrp:overview"].forEach((k) => localStorage.removeItem(k));
    localStorage.setItem("vrp:version", APP_VERSION);
  }

  const DEFAULT_API = window.location.origin;
  const API = window.__API_BASE__ || DEFAULT_API;
  const API_URL = new URL(API);
  const API_V1 = `${API}/api/v1`;
  const API_VRP = `${API_V1}/vrp`;
  const API_HEALTH = `${API_V1}/health`;
  const WS_BASE = `${API_URL.protocol === "https:" ? "wss" : "ws"}://${API_URL.host}`;
  const h3 = window.h3 || null;
  const L = window.L || null;
  const { createElement: el, useEffect, useMemo, useRef, useState, useCallback } = React;

  const PALETTE = ["#6366f1", "#22c55e", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899", "#06b6d4", "#f97316"];

  /* ── Task type color mapping ──────────────────────────────────────── */
  const TASK_TYPE_COLORS = {
    credit_investigation: "#3b82f6",  // blue
    skips_collect: "#22c55e",          // green
    demand_letter: "#eab308",          // yellow
  };
  const TASK_TYPE_LABELS = {
    credit_investigation: "Credit Investigation",
    skips_collect: "Skips & Collect",
    demand_letter: "Demand Letter",
  };
  const FIELDMAN_COLOR = "#ef4444";  // red

  // Short type labels for tooltip badges
  const TASK_TYPE_SHORT = {
    credit_investigation: "CI",
    skips_collect: "S&C",
    demand_letter: "DL",
    field_verification: "FV",
    collection: "COL",
    skip_trace: "ST",
    repossession: "REPO",
  };

  // Rich tooltip builders (used by map markers)
  const buildTaskTooltip = (t, seq, color) => {
    const typeShort = TASK_TYPE_SHORT[t.task_type] || (t.task_type || "TASK").toUpperCase().slice(0, 4);
    const mp = t.manual_priority != null;
    const prioStr = mp
      ? `<span class="tt-manual">★ ${t.manual_priority}</span>`
      : (t.priority != null ? t.priority.toFixed(1) : "-");
    const bankRow = t.bank ? `<div class="tt-row"><span class="tt-label">Bank</span><span class="tt-value">${t.bank}</span></div>` : "";
    const distRow = t.distance != null ? `<div class="tt-row"><span class="tt-label">Distance</span><span class="tt-value">${(t.distance / 1000).toFixed(2)} km</span></div>` : "";
    const durRow = t.duration != null ? `<div class="tt-row"><span class="tt-label">Duration</span><span class="tt-value">${Math.round(t.duration / 60)} min</span></div>` : "";
    const svcRow = t.service ? `<div class="tt-row"><span class="tt-label">Service</span><span class="tt-value">${Math.round(t.service / 60)} min</span></div>` : "";
    const isCompleted = t.status === "completed";
    const statusRow = t.status ? `<div class="tt-row"><span class="tt-label">Status</span><span class="tt-value" style="color:${isCompleted ? "#22c55e" : "#6366f1"};font-weight:600">${isCompleted ? "Completed ✓" : "Pending"}</span></div>` : "";
    const completedRow = isCompleted && t.completed_at ? `<div class="tt-row"><span class="tt-label">Done</span><span class="tt-value">${new Date(t.completed_at).toLocaleString()}</span></div>` : "";
    return `<div class="tt-header"><span class="tt-type">${typeShort}</span> #${seq} — ${t.address || "Unknown"}</div><div class="tt-uuid">${t.task_id || ""}</div>${statusRow}<div class="tt-row"><span class="tt-label">Priority</span><span class="tt-value">${prioStr}</span></div>${bankRow}${distRow}${durRow}${svcRow}${completedRow}`;
  };

  const buildFieldmanTooltip = (fid, color, opts) => {
    const taskCount = opts.tasks != null ? opts.tasks : "";
    const dist = opts.distance != null ? `<div class="tt-row"><span class="tt-label">Distance</span><span class="tt-value">${(opts.distance / 1000).toFixed(1)} km</span></div>` : "";
    const dur = opts.duration != null ? `<div class="tt-row"><span class="tt-label">Duration</span><span class="tt-value">${Math.round(opts.duration / 60)} min</span></div>` : "";
    const tasksRow = taskCount !== "" ? `<div class="tt-row"><span class="tt-label">Tasks</span><span class="tt-value">${taskCount}</span></div>` : "";
    return `<div class="tt-header"><span style="color:${color || "#ef4444"}">●</span> Fieldman Start</div><div class="tt-uuid">${fid}</div>${tasksRow}${dist}${dur}`;
  };

  /* ── SVG Icon System (inline, minimalistic line icons) ────────────── */
  const SVG = (d, size = 16) => `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
  const ICONS = {
    dashboard:    SVG('<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>'),
    generator:    SVG('<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="7.5 4.21 12 6.81 16.5 4.21"/><polyline points="7.5 19.79 7.5 14.6 3 12"/><polyline points="21 12 16.5 14.6 16.5 19.79"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>'),
    plan:         SVG('<polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"/><line x1="8" y1="2" x2="8" y2="18"/><line x1="16" y1="6" x2="16" y2="22"/>'),
    jobs:         SVG('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>'),
    map:          SVG('<circle cx="12" cy="10" r="3"/><path d="M12 21.7C17.3 17 20 13 20 10a8 8 0 1 0-16 0c0 3 2.7 7 8 11.7z"/>'),
    realtime:     SVG('<path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><circle cx="12" cy="20" r="1"/>'),
    metrics:      SVG('<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>'),
    health:       SVG('<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>'),
    target:       SVG('<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>'),
    clipboard:    SVG('<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/>'),
    tasks:        SVG('<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>'),
    truck:        SVG('<rect x="1" y="3" width="15" height="13"/><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/>'),
    search:       SVG('<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>'),
    rocket:       SVG('<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="M12 15l-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>'),
    key:          SVG('<path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/>'),
    eye:          SVG('<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>'),
    box:          SVG('<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>'),
    check:        SVG('<polyline points="20 6 9 17 4 12"/>'),
    x:            SVG('<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>'),
    trash:        SVG('<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'),
    upload:       SVG('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>'),
    download:     SVG('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>'),
    plug:         SVG('<path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/><path d="M18 8v5a6 6 0 0 1-12 0V8z"/>'),
    scroll:       SVG('<path d="M8 21h12a2 2 0 0 0 2-2v-2H10v2a2 2 0 1 1-4 0V5a2 2 0 0 0-2-2H2"/><path d="M19 17V5a2 2 0 0 0-2-2H4"/>'),
    sliders:      SVG('<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>'),
    refresh:      SVG('<polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>'),
    ruler:        SVG('<path d="M16 3l5 5-12 12-5-5z"/><path d="M8 7l2 2"/><path d="M11 10l2 2"/><path d="M14 13l2 2"/>'),
    grid:         SVG('<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>'),
    gridHide:     SVG('<rect x="3" y="3" width="7" height="7" opacity="0.3"/><rect x="14" y="3" width="7" height="7" opacity="0.3"/><rect x="3" y="14" width="7" height="7" opacity="0.3"/><rect x="14" y="14" width="7" height="7" opacity="0.3"/><line x1="2" y1="2" x2="22" y2="22"/>'),
    play:         SVG('<polygon points="5 3 19 12 5 21 5 3"/>'),
    pause:        SVG('<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>'),
    motorcycle:   SVG('<path d="M5 16a3 3 0 1 0 0 6 3 3 0 0 0 0-6zm14 0a3 3 0 1 0 0 6 3 3 0 0 0 0-6z"/><path d="M5 19h4l3-8h4l3 8"/><circle cx="16" cy="9" r="2.5"/><path d="M10 11l2-5h3"/>'),
  };
  const icon = (name, size) => ICONS[name] || "";

  /* ── Navigation config ────────────────────────────────────────────── */
  const NAV_PAGES = [
    { id: "dashboard", label: "Dashboard", icon: icon("dashboard"), section: "Overview" },
    { id: "map", label: "Map View", icon: icon("map"), section: "Operations" },
    { id: "jobs", label: "Job Control", icon: icon("jobs"), section: "Operations" },
    { id: "realtime", label: "Realtime", icon: icon("realtime"), section: "Monitoring" },
    { id: "metrics", label: "Route Metrics", icon: icon("metrics"), section: "Monitoring" },
    { id: "fieldman_tasks", label: "Fieldman Tasks", icon: icon("tasks"), section: "Operations" },
    { id: "health", label: "System Health", icon: icon("health"), section: "System" },
  ];

  /* ── API utilities ────────────────────────────────────────────────── */
  async function fetchJson(url, options) {
    const response = await fetch(url, options);
    let data = null;
    try { data = await response.json(); } catch (_) { data = null; }
    if (!response.ok) {
      const detail = data && (data.detail || data.message) ? data.detail || data.message : null;
      throw new Error(detail || `Request failed (${response.status})`);
    }
    return data;
  }

  /* ── Request-Level Cache & Dedup ──────────────────────────────────── */
  const _requestCache = new Map();   // key → { data, ts }
  const _inflightRequests = new Map(); // key → Promise
  const _REQUEST_CACHE_MAX = 100; // LRU pruning threshold

  /** Prune stale entries when cache exceeds max size. */
  function _pruneRequestCache() {
    if (_requestCache.size <= _REQUEST_CACHE_MAX) return;
    // Remove oldest entries until at 75% capacity
    const entries = Array.from(_requestCache.entries())
      .sort((a, b) => a[1].ts - b[1].ts);
    const toRemove = entries.slice(0, entries.length - Math.floor(_REQUEST_CACHE_MAX * 0.75));
    toRemove.forEach(([key]) => _requestCache.delete(key));
  }

  /**
   * Cached GET with in-flight deduplication and TTL.
   * - Same URL within ttlMs returns cached data (no network).
   * - Concurrent identical calls share a single network request.
   * - Mutations should call `invalidateCache(pattern)` to bust stale data.
   */
  async function cachedFetch(url, { ttlMs = 10000 } = {}) {
    const hit = _requestCache.get(url);
    if (hit && (Date.now() - hit.ts) < ttlMs) return hit.data;

    // Dedup: if an identical request is already in-flight, share it
    if (_inflightRequests.has(url)) return _inflightRequests.get(url);

    const promise = fetchJson(url)
      .then((data) => {
        _requestCache.set(url, { data, ts: Date.now() });
        _inflightRequests.delete(url);
        _pruneRequestCache();
        return data;
      })
      .catch((err) => {
        _inflightRequests.delete(url);
        throw err;
      });
    _inflightRequests.set(url, promise);
    return promise;
  }

  /** Invalidate cache entries matching a substring or pattern. */
  function invalidateCache(pattern) {
    if (!pattern) { _requestCache.clear(); return; }
    for (const key of _requestCache.keys()) {
      if (key.includes(pattern)) _requestCache.delete(key);
    }
  }

  /* ── Toast Notification System ─────────────────────────────────── */
  const _toastContainer = (() => {
    let el = document.getElementById('toast-container');
    if (!el) {
      el = document.createElement('div');
      el.id = 'toast-container';
      el.className = 'toast-container';
      document.body.appendChild(el);
    }
    return el;
  })();

  /**
   * Show a toast notification.
   * @param {string} message - Text to display.
   * @param {'success'|'error'|'warning'|'info'} type - Toast style.
   * @param {number} durationMs - Auto-dismiss delay (0 = manual close only).
   */
  function showToast(message, type = 'info', durationMs = 4000) {
    const toast = document.createElement('div');
    toast.className = `toast toast--${type}`;
    toast.innerHTML = `<span>${message}</span><button class="toast-close">&times;</button>`;
    const close = toast.querySelector('.toast-close');
    const dismiss = () => {
      toast.classList.add('toast-exit');
      setTimeout(() => toast.remove(), 200);
    };
    close.addEventListener('click', dismiss);
    _toastContainer.appendChild(toast);
    if (durationMs > 0) setTimeout(dismiss, durationMs);
    // Limit visible toasts
    while (_toastContainer.children.length > 5) {
      _toastContainer.firstChild.remove();
    }
  }

  /** Debounce helper — returns a debounced version of fn. */
  function debounce(fn, delayMs) {
    let timer = null;
    return function (...args) {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => { timer = null; fn.apply(this, args); }, delayMs);
    };
  }

  /** Throttle helper — calls fn at most once per intervalMs. */
  function throttle(fn, intervalMs) {
    let last = 0;
    let pending = null;
    return function (...args) {
      const now = Date.now();
      if (now - last >= intervalMs) {
        last = now;
        fn.apply(this, args);
      } else if (!pending) {
        pending = setTimeout(() => {
          last = Date.now();
          pending = null;
          fn.apply(this, args);
        }, intervalMs - (now - last));
      }
    };
  }

  function toCsv(value) {
    if (!value) return null;
    return value.split(",").map((s) => s.trim()).filter((s) => s.length > 0);
  }

  function safeJsonParse(value) {
    if (!value) return null;
    try { return JSON.parse(value); } catch (_) { return null; }
  }

  function formatNum(value, unit) {
    if (value == null) return "-";
    return `${value.toFixed(2)}${unit || ""}`;
  }

  function timestamp() {
    return new Date().toLocaleTimeString();
  }

  /* ── Client-side H3 resolution suggestion (mirrors backend logic) ── */
  function suggestH3Resolution(points, zoom) {
    if (!points || points.length === 0) return 7;
    if (points.length === 1) return 9;
    const lats = points.map(p => p.lat);
    const lngs = points.map(p => p.lng);
    const minLat = Math.min(...lats), maxLat = Math.max(...lats);
    const minLng = Math.min(...lngs), maxLng = Math.max(...lngs);
    // Haversine-based bbox diagonal in km
    const toRad = d => d * Math.PI / 180;
    const dlat = toRad(maxLat - minLat);
    const dlng = toRad(maxLng - minLng);
    const a = Math.sin(dlat/2)**2 + Math.cos(toRad(minLat)) * Math.cos(toRad(maxLat)) * Math.sin(dlng/2)**2;
    const diagKm = 6371 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));

    // Base resolution from geographic spread
    let res;
    if (diagKm < 2) res = 10;
    else if (diagKm < 5) res = 9;
    else if (diagKm < 10) res = 8;
    else if (diagKm < 30) res = 7;
    else if (diagKm < 80) res = 7;
    else if (diagKm < 200) res = 6;
    else res = 5;

    // Density refinement: more points in a small area → finer cells
    const areaKm2 = Math.max(diagKm * diagKm * 0.5, 0.01);
    const density = points.length / areaKm2;
    if (density > 200 && res < 11) res += 2;
    else if (density > 50 && res < 10) res += 1;
    else if (density > 20 && res < 10) res += 1;

    // Point count influence: many points benefit from finer resolution
    const n = points.length;
    if (n > 500 && res < 10) res = Math.max(res, 8);
    if (n > 200 && res < 9) res = Math.max(res, 7);
    if (n > 1000 && res < 11) res += 1;

    // Zoom level influence: if user is zoomed in, prefer finer resolution
    if (zoom != null) {
      const zoomSuggested = Math.max(4, Math.min(12, Math.floor(zoom / 2) + 3));
      // Blend: weight data-based 60%, zoom-based 40%
      res = Math.round(res * 0.6 + zoomSuggested * 0.4);
    }

    // Clamp to reasonable range
    return Math.max(4, Math.min(12, res));
  }

  /* ── H3 grid builder ──────────────────────────────────────────────── */
  // Legacy client-side builder kept as fallback
  // ── Performance: debounce helper ────────────────────────────────
  function useDebounce(value, delay) {
    const [debounced, setDebounced] = useState(value);
    useEffect(() => {
      const timer = setTimeout(() => setDebounced(value), delay);
      return () => clearTimeout(timer);
    }, [value, delay]);
    return debounced;
  }

  // H3 backend cache (keyed by resolution, expires after 60s, max 20 entries)
  const _h3Cache = {};
  const H3_CACHE_TTL = 60000;
  const H3_CACHE_MAX = 20;

  function _pruneH3Cache() {
    const keys = Object.keys(_h3Cache);
    if (keys.length <= H3_CACHE_MAX) return;
    // Remove expired first, then oldest
    const now = Date.now();
    keys.forEach(k => { if (now - _h3Cache[k].ts > H3_CACHE_TTL) delete _h3Cache[k]; });
    const remaining = Object.keys(_h3Cache);
    if (remaining.length > H3_CACHE_MAX) {
      remaining.sort((a, b) => _h3Cache[a].ts - _h3Cache[b].ts);
      remaining.slice(0, remaining.length - H3_CACHE_MAX).forEach(k => delete _h3Cache[k]);
    }
  }

  function buildH3Grid(points, resolution) {
    if (!h3 || !points.length) return [];
    const cells = new Set();
    points.forEach((p) => {
      if (p.lat != null && p.lng != null) cells.add(h3.latLngToCell(p.lat, p.lng, resolution));
    });
    return Array.from(cells).map((cell) => {
      const boundary = h3.cellToBoundary(cell, true);
      const coords = boundary.map((c) => [c[1], c[0]]);
      coords.push(coords[0]);
      return { type: "Feature", properties: { cell }, geometry: { type: "Polygon", coordinates: [coords] } };
    });
  }

  // Backend-driven H3 grid fetch (with client-side cache)
  async function fetchH3Grid(resolution, includeTasks = true, includeFieldmen = true) {
    const cacheKey = `${resolution}:${includeTasks}:${includeFieldmen}`;
    const cached = _h3Cache[cacheKey];
    if (cached && Date.now() - cached.ts < H3_CACHE_TTL) return cached.data;
    try {
      const resp = await fetch(`${API}/api/v1/vrp/h3/grid`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resolution: Number(resolution), include_tasks: includeTasks, include_fieldmen: includeFieldmen }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      // Handle both direct response and envelope format
      const cells = data.cells || (data.data && data.data.cells) || [];
      const result = cells.map((c) => {
        // boundary is [[lat,lng],...] — convert to GeoJSON [lng,lat]
        const coords = c.boundary.map((pt) => [pt[1], pt[0]]);
        coords.push(coords[0]); // close ring
        return {
          type: "Feature",
          properties: { cell: c.cell, task_count: c.task_count, fieldman_count: c.fieldman_count, center_lat: c.center_lat, center_lng: c.center_lng },
          geometry: { type: "Polygon", coordinates: [coords] },
        };
      });
      _h3Cache[cacheKey] = { data: result, ts: Date.now() };
      _pruneH3Cache();
      return result;
    } catch (err) {
      console.error("H3 grid fetch error:", err);
      return null; // null signals fallback
    }
  }

  /* ── Leaflet map hook ─────────────────────────────────────────────── */

  /** Simplify polyline coordinates using Ramer-Douglas-Peucker (tolerance in degrees). */
  function simplifyCoords(coords, tolerance) {
    if (!coords || coords.length <= 2) return coords;
    const sqTol = tolerance * tolerance;
    function sqDist(p, a, b) {
      let dx = b[0] - a[0], dy = b[1] - a[1];
      if (dx !== 0 || dy !== 0) {
        const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)));
        dx = a[0] + t * dx; dy = a[1] + t * dy;
      } else { dx = a[0]; dy = a[1]; }
      return (p[0] - dx) ** 2 + (p[1] - dy) ** 2;
    }
    function rdp(pts, first, last, sqTolerance) {
      let maxSq = sqTolerance, index = 0;
      for (let i = first + 1; i < last; i++) {
        const sq = sqDist(pts[i], pts[first], pts[last]);
        if (sq > maxSq) { index = i; maxSq = sq; }
      }
      const result = [];
      if (maxSq > sqTolerance) {
        if (index - first > 1) result.push(...rdp(pts, first, index, sqTolerance));
        result.push(pts[index]);
        if (last - index > 1) result.push(...rdp(pts, index, last, sqTolerance));
      }
      return result;
    }
    const simplified = [coords[0], ...rdp(coords, 0, coords.length - 1, sqTol), coords[coords.length - 1]];
    return simplified;
  }

  /** Hook: returns true when the document/tab is visible. */
  function usePageVisible() {
    const [visible, setVisible] = useState(!document.hidden);
    useEffect(() => {
      const handler = () => setVisible(!document.hidden);
      document.addEventListener("visibilitychange", handler);
      return () => document.removeEventListener("visibilitychange", handler);
    }, []);
    return visible;
  }

  function useLeafletMap(containerRef, onReady, onTileStatus) {
    const mapRef = useRef(null);
    const readyCb = useRef(onReady);
    const tileCb = useRef(onTileStatus);
    readyCb.current = onReady;
    tileCb.current = onTileStatus;

    useEffect(() => {
      if (!containerRef.current || mapRef.current || !L) return;
      let destroyed = false;
      let tileErr = 0, tileOk = 0, usingFallback = false;
      const container = containerRef.current;
      const map = L.map(container, { zoomControl: false, preferCanvas: true });

      const mapPane = map.getPane("mapPane");
      if (mapPane && !mapPane._leaflet_pos) L.DomUtil.setPosition(mapPane, L.point(0, 0));

      map.setView([14.5995, 120.9842], 11);
      L.control.zoom({ position: "bottomright" }).addTo(map);

      const report = () => {
        if (tileCb.current) tileCb.current({ loaded: tileOk, failed: tileErr, fallback: usingFallback, size: map.getSize() });
      };

      const tiles = L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
        attribution: "&copy; OpenStreetMap &copy; CARTO",
        maxZoom: 19,
        subdomains: "abcd",
      });
      tiles.on("tileload", () => { tileOk++; report(); });
      tiles.on("tileerror", () => {
        tileErr++;
        report();
        if (!usingFallback && tileErr >= 3) {
          usingFallback = true;
          map.removeLayer(tiles);
          const fb = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            attribution: "&copy; OpenStreetMap",
            maxZoom: 19,
          }).addTo(map);
          fb.on("tileload", () => { tileOk++; report(); });
          fb.on("tileerror", () => { tileErr++; report(); });
        }
      });
      tiles.addTo(map);

      const layers = {
        routes: L.layerGroup().addTo(map),
        tasks: L.layerGroup().addTo(map),
        fieldmen: L.layerGroup().addTo(map),
        overviewTasks: L.layerGroup().addTo(map),
        overviewFieldmen: L.layerGroup().addTo(map),
        h3: L.layerGroup().addTo(map),
      };

      mapRef.current = { map, layers };
      report();
      setTimeout(() => { if (!destroyed) readyCb.current(); }, 0);
      setTimeout(() => {
        if (destroyed) return;
        map.invalidateSize();
        tiles.redraw();
      }, 300);

      const onResize = () => { if (!destroyed && mapRef.current) mapRef.current.map.invalidateSize(); };
      window.addEventListener("resize", onResize);

      return () => {
        destroyed = true;
        window.removeEventListener("resize", onResize);
        if (mapRef.current) { mapRef.current.map.remove(); mapRef.current = null; }
      };
    }, []);

    return mapRef;
  }

  /* ═══════════════════════════════════════════════════════════════════
     SHARED STATE STORE
     ═══════════════════════════════════════════════════════════════════ */
  function useAppState() {
    // Persist current page in URL hash so refresh keeps the same page
    const validPages = new Set(NAV_PAGES.map(p => p.id));
    const hashPage = window.location.hash.replace("#", "");
    const initialPage = validPages.has(hashPage) ? hashPage : "dashboard";

    const [page, _setPage] = useState(initialPage);
    const setPage = useCallback((p) => {
      _setPage(p);
      window.location.hash = p;
    }, []);
    const [statusOk, setStatusOk] = useState(false);
    const [statusText, setStatusText] = useState("Connecting...");

    // Restore persisted state from localStorage
    const _stored = (key, fallback) => {
      try { const v = localStorage.getItem(`vrp:${key}`); return v ? JSON.parse(v) : fallback; } catch (_) { return fallback; }
    };
    const [jobId, _setJobId] = useState(() => _stored("jobId", ""));
    const [routes, _setRoutes] = useState(() => _stored("routes", []));
    const [routeSummaries, _setRouteSummaries] = useState(() => _stored("routeSummaries", []));
    const [overview, _setOverview] = useState({ tasks: [], fieldmen: [] });  // Always start empty; fetched fresh from backend on mount

    // Throttled localStorage writers to avoid GC pressure during rapid updates
    const _lsThrottle = useRef({});
    const _lsWrite = useCallback((key, value) => {
      if (_lsThrottle.current[key]) clearTimeout(_lsThrottle.current[key]);
      _lsThrottle.current[key] = setTimeout(() => {
        try { localStorage.setItem(`vrp:${key}`, JSON.stringify(value)); } catch(_){}
        delete _lsThrottle.current[key];
      }, 500);
    }, []);

    // Wrapped setters that also persist to localStorage (throttled)
    const setJobId = useCallback((v) => { _setJobId(v); try { localStorage.setItem("vrp:jobId", JSON.stringify(v)); } catch(_){} }, []);
    const setRoutes = useCallback((v) => { _setRoutes(v); _lsWrite("routes", v); }, [_lsWrite]);
    const setRouteSummaries = useCallback((v) => { _setRouteSummaries(v); _lsWrite("routeSummaries", v); }, [_lsWrite]);
    const setOverview = useCallback((v) => { _setOverview(v); }, []);  // Don't persist 20k records to localStorage

    const [assignmentStrategy, setAssignmentStrategy] = useState("h3");
    const [h3Resolution, setH3Resolution] = useState(7);
    const [h3AutoResolution, setH3AutoResolution] = useState(true);
    const [h3ResolutionInfo, setH3ResolutionInfo] = useState(null);
    const [jobResult, setJobResult] = useState("");
    const [logs, setLogs] = useState([]);
    const [showH3, setShowH3] = useState(true);
    const [animateRoutes, setAnimateRoutes] = useState(true);
    const [tileStatus, setTileStatus] = useState({ loaded: 0, failed: 0, fallback: false, size: null });
    const [taskSummary, setTaskSummary] = useState({ total_tasks: 0, total_fieldmen: 0, by_type: {}, by_bank: {} });
    const [jobListVersion, setJobListVersion] = useState(0);
    const wsRef = useRef(null);

    const appendLog = useCallback((msg) => {
      setLogs((prev) => [`[${timestamp()}] ${msg}`, ...prev].slice(0, 60));
    }, []);

    const bumpJobList = useCallback(() => setJobListVersion(v => v + 1), []);

    return {
      page, setPage, statusOk, setStatusOk, statusText, setStatusText,
      jobId, setJobId, assignmentStrategy, setAssignmentStrategy,
      h3Resolution, setH3Resolution, h3AutoResolution, setH3AutoResolution,
      h3ResolutionInfo, setH3ResolutionInfo,
      jobResult, setJobResult, routes, setRoutes, overview, setOverview,
      routeSummaries, setRouteSummaries, logs, setLogs,
      showH3, setShowH3, animateRoutes, setAnimateRoutes,
      tileStatus, setTileStatus, taskSummary, setTaskSummary,
      jobListVersion, bumpJobList,
      wsRef, appendLog,
    };
  }

  /* ═══════════════════════════════════════════════════════════════════
     COMPONENTS
     ═══════════════════════════════════════════════════════════════════ */

  /* ── Sidebar ──────────────────────────────────────────────────────── */
  const Sidebar = React.memo(function Sidebar({ activePage, onNavigate, routeCount, logCount }) {
    let lastSection = "";
    const items = NAV_PAGES.map((nav) => {
      const parts = [];
      if (nav.section !== lastSection) {
        lastSection = nav.section;
        parts.push(el("div", { key: `sec-${nav.section}`, className: "nav-section-label" }, nav.section));
      }
      let badge = null;
      if (nav.id === "metrics" && routeCount > 0) badge = el("span", { className: "nav-badge" }, routeCount);
      if (nav.id === "realtime" && logCount > 0) badge = el("span", { className: "nav-badge" }, logCount);

      parts.push(
        el("button", {
          key: nav.id,
          className: `nav-item ${activePage === nav.id ? "active" : ""}`,
          onClick: () => onNavigate(nav.id),
        },
          el("span", { className: "nav-icon", dangerouslySetInnerHTML: { __html: nav.icon } }),
          el("span", null, nav.label),
          badge
        )
      );
      return parts;
    }).flat();

    return el("nav", { className: "sidebar" },
      el("div", { className: "nav-section" }, ...items),
      el("div", { className: "sidebar-footer" },
        el("div", { className: "sidebar-version" }, "VRP Console v2.0")
      )
    );
  });

  /* ── Topbar ───────────────────────────────────────────────────────── */
  const Topbar = React.memo(function Topbar({ statusOk, statusText }) {
    return el("header", { className: "topbar" },
      el("div", { className: "topbar-left" },
        el("div", { className: "brand-logo" }, "VRP"),
        el("div", { className: "brand-text" },
          el("h1", null, "H3 + OSRM Routing"),
          el("p", null, "VROOM Assignment Console")
        )
      ),
      el("div", { className: "topbar-right" },
        el("div", { className: "status-badge" },
          el("span", { className: `status-dot ${statusOk ? "ok" : ""}` }),
          el("span", null, statusText)
        )
      )
    );
  });

  /* ═══════════════════════════════════════════════════════════════════
     PAGE: Dashboard
     ═══════════════════════════════════════════════════════════════════ */
  const DashboardPage = React.memo(function DashboardPage({ state }) {
    const { routes, overview, routeSummaries, statusOk, logs, taskSummary, setTaskSummary } = state;
    const totalDist = routeSummaries.reduce((s, r) => s + (r.distance || 0), 0);
    const totalDur = routeSummaries.reduce((s, r) => s + (r.duration || 0), 0);
    const totalTasks = routeSummaries.reduce((s, r) => s + (r.tasks || 0), 0);

    // Fetch task summary on mount (cached)
    useEffect(() => {
      cachedFetch(`${API_VRP}/task-summary`, { ttlMs: 10000 }).then((d) => setTaskSummary(d)).catch(() => {});
    }, []);

    const byType = taskSummary.by_type || {};
    const hasData = overview.tasks.length > 0 || overview.fieldmen.length > 0;

    return el("div", null,
      el("div", { className: "page-header" },
        el("h2", null, "Dashboard"),
        el("p", null, "System overview and key metrics at a glance")
      ),

      !hasData && el("div", { className: "empty-state", style: { marginBottom: 24 } },
        el("div", { className: "icon" }, "\uD83D\uDCCA"),
        el("p", null, "No data yet. Go to Map View and click Randomize to generate tasks and fieldmen.")
      ),

      // Task type breakdown cards
      el("div", { className: "stats-row" },
        el("div", { className: "stat-card", style: { borderLeft: `4px solid ${TASK_TYPE_COLORS.credit_investigation}` } },
          el("div", { className: "stat-value", style: { color: TASK_TYPE_COLORS.credit_investigation } },
            byType.credit_investigation || 0),
          el("div", { className: "stat-label" },
            el("span", { className: "type-dot", style: { background: TASK_TYPE_COLORS.credit_investigation } }),
            " Credit Investigation")
        ),
        el("div", { className: "stat-card", style: { borderLeft: `4px solid ${TASK_TYPE_COLORS.skips_collect}` } },
          el("div", { className: "stat-value", style: { color: TASK_TYPE_COLORS.skips_collect } },
            byType.skips_collect || 0),
          el("div", { className: "stat-label" },
            el("span", { className: "type-dot", style: { background: TASK_TYPE_COLORS.skips_collect } }),
            " Skips & Collect")
        ),
        el("div", { className: "stat-card", style: { borderLeft: `4px solid ${TASK_TYPE_COLORS.demand_letter}` } },
          el("div", { className: "stat-value", style: { color: TASK_TYPE_COLORS.demand_letter } },
            byType.demand_letter || 0),
          el("div", { className: "stat-label" },
            el("span", { className: "type-dot", style: { background: TASK_TYPE_COLORS.demand_letter } }),
            " Demand Letter")
        ),
        el("div", { className: "stat-card", style: { borderLeft: `4px solid ${FIELDMAN_COLOR}` } },
          el("div", { className: "stat-value", style: { color: FIELDMAN_COLOR } },
            taskSummary.total_fieldmen || 0),
          el("div", { className: "stat-label" },
            el("span", { className: "type-dot", style: { background: FIELDMAN_COLOR } }),
            " Fieldmen")
        )
      ),

      el("div", { className: "stats-row" },
        el("div", { className: "stat-card" },
          el("div", { className: "stat-value" }, routes.length),
          el("div", { className: "stat-label" }, "Active Routes")
        ),
        el("div", { className: "stat-card" },
          el("div", { className: "stat-value" }, totalTasks),
          el("div", { className: "stat-label" }, "Tasks Assigned")
        ),
        el("div", { className: "stat-card" },
          el("div", { className: "stat-value" }, overview.fieldmen.length),
          el("div", { className: "stat-label" }, "Fieldmen")
        ),
        el("div", { className: "stat-card" },
          el("div", { className: "stat-value" }, overview.tasks.length),
          el("div", { className: "stat-label" }, "All Tasks")
        ),
        el("div", { className: "stat-card" },
          el("div", { className: "stat-value" }, formatNum(totalDist / 1000, " km")),
          el("div", { className: "stat-label" }, "Total Distance")
        ),
        el("div", { className: "stat-card" },
          el("div", { className: "stat-value" }, formatNum(totalDur / 60, " min")),
          el("div", { className: "stat-label" }, "Total Duration")
        )
      ),
      el("div", { className: "dashboard-grid" },
        el("div", { className: "card" },
          el("div", { className: "card-header" },
            el("span", { className: "card-title" }, el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("dashboard") } }), "System Status")
          ),
          el("div", { className: "health-grid" },
            el("div", { className: "health-item" },
              el("div", { className: `health-dot ${statusOk ? "ok" : "err"}` }),
              el("div", null,
                el("div", { className: "health-label" }, "API Server"),
                el("div", { className: "health-sub" }, statusOk ? "Healthy" : "Unreachable")
              )
            ),
            el("div", { className: "health-item" },
              el("div", { className: `health-dot ${statusOk ? "ok" : "err"}` }),
              el("div", null,
                el("div", { className: "health-label" }, "Database"),
                el("div", { className: "health-sub" }, statusOk ? "Connected" : "Disconnected")
              )
            )
          )
        ),
        el("div", { className: "card" },
          el("div", { className: "card-header" },
            el("span", { className: "card-title" }, el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("realtime") } }), "Recent Events")
          ),
          logs.length === 0
            ? el("div", { className: "empty-state" }, el("p", null, "No events yet. Connect via Realtime page."))
            : el("div", { className: "log-console" }, logs.slice(0, 10).join("\n"))
        ),
        routeSummaries.length > 0 && el("div", { className: "card full-width" },
          el("div", { className: "card-header" },
            el("span", { className: "card-title" }, el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("truck") } }), "Route Summary")
          ),
          el("table", { className: "data-table" },
            el("thead", null,
              el("tr", null,
                el("th", null, "Fieldman"),
                el("th", null, "Tasks"),
                el("th", null, "Distance"),
                el("th", null, "Duration")
              )
            ),
            el("tbody", null,
              routeSummaries.map((r, i) =>
                el("tr", { key: i },
                  el("td", null, r.fieldman_id),
                  el("td", null, r.tasks),
                  el("td", null, formatNum(r.distance, " m")),
                  el("td", null, formatNum(r.duration / 60, " min"))
                )
              )
            )
          )
        )
      )
    );
  });

  /* ═══════════════════════════════════════════════════════════════════
     PAGE: Job Control
     ═══════════════════════════════════════════════════════════════════ */
  const JobsPage = React.memo(function JobsPage({ state }) {
    const {
      jobId, setJobId, jobResult, setJobResult, setRoutes, setRouteSummaries,
      setOverview, appendLog,
    } = state;

    const [loading, setLoading] = useState(false);
    const [jobList, setJobList] = useState([]);
    const [selectedJob, setSelectedJob] = useState(null);
    const [showModal, setShowModal] = useState(false);
    const [autoRefresh, setAutoRefresh] = useState(false);
    const [refreshSec, setRefreshSec] = useState(10);

    // Fetch job list
    const fetchJobList = async () => {
      try {
        const data = await fetchJson(`${API_VRP}/jobs?limit=50`);
        setJobList(Array.isArray(data) ? data : []);
      } catch (_) {}
    };

    useEffect(() => {
      fetchJobList();
      // Always fetch fresh overview on mount (don't trust stale localStorage)
      fetchJson(`${API_VRP}/overview`)
        .then(o => { if (o && (o.tasks || o.fieldmen)) setOverview(o); })
        .catch(() => {});
      // Clear any lingering stale overview from old localStorage versions
      try { localStorage.removeItem("vrp:overview"); } catch(_){}
    }, [state.jobListVersion]);

    // Auto-refresh interval — visibility-aware
    useEffect(() => {
      if (!autoRefresh) return;
      const iv = setInterval(() => {
        if (document.hidden) return; // Skip when tab not visible
        fetchJobList();
        if (jobId) {
          fetchJson(`${API_VRP}/jobs/${jobId}`).then(d => {
            setJobResult(JSON.stringify(d, null, 2));
          }).catch(() => {});
        }
      }, refreshSec * 1000);
      return () => clearInterval(iv);
    }, [autoRefresh, refreshSec, jobId]);

    const action = async (label, fn) => {
      setLoading(true);
      try {
        await fn();
        appendLog(`${label} completed`);
      } catch (err) {
        setJobResult(err.message);
        appendLog(`${label} failed: ${err.message}`);
      } finally { setLoading(false); }
    };

    const handleStatus = (jid) => action("Status check", async () => {
      const id = jid || jobId;
      if (!id) return;
      const data = await fetchJson(`${API_VRP}/jobs/${id}`);
      setJobResult(JSON.stringify(data, null, 2));
      setSelectedJob(data);
    });

    const handleMetrics = (jid) => action("Metrics fetch", async () => {
      const id = jid || jobId;
      if (!id) return;
      const data = await fetchJson(`${API_VRP}/jobs/${id}/metrics`);
      setJobResult(JSON.stringify(data, null, 2));
    });

    const handlePreview = (jid) => action("Preview load", async () => {
      const id = jid || jobId;
      if (!id) return;
      const data = await fetchJson(`${API_VRP}/jobs/${id}/preview`);
      const loadedRoutes = data.routes || [];
      setRoutes(loadedRoutes);
      setRouteSummaries(
        loadedRoutes.map((r) => ({
          fieldman_id: r.fieldman_id, distance: r.distance, duration: r.duration, tasks: r.tasks.length,
        }))
      );
      setJobResult(JSON.stringify({ job_id: data.job_id, status: data.status, routes: loadedRoutes.length }, null, 2));
    });

    const handleFinalize = (jid) => action("Finalize", async () => {
      const id = jid || jobId;
      if (!id) return;
      const data = await fetchJson(`${API_VRP}/jobs/${id}/finalize`, { method: "POST" });
      setJobResult(JSON.stringify(data, null, 2));
      fetchJobList();
    });

    const handleLoadAll = () => action("Load all assignments", async () => {
      const data = await fetchJson(`${API_VRP}/assignments?include_geometry=true&include_overview=true`);
      const loadedRoutes = (data.routes || []).map((r) => {
        const geom = r.geometry;
        // Normalize geometry: may be a raw coord array, a GeoJSON object, or null
        let coords = [];
        if (Array.isArray(geom)) coords = geom;
        else if (geom && Array.isArray(geom.coordinates)) coords = geom.coordinates;
        return { ...r, geometry: { type: "LineString", coordinates: coords } };
      });
      setRoutes(loadedRoutes);
      setOverview(data.overview || { tasks: [], fieldmen: [] });
      setRouteSummaries(
        loadedRoutes.map((r) => ({
          fieldman_id: r.fieldman_id,
          distance: r.tasks.reduce((s, t) => s + (t.distance || 0), 0),
          duration: r.tasks.reduce((s, t) => s + (t.duration || 0), 0),
          tasks: r.tasks.length,
        }))
      );
      setJobResult(`Loaded ${loadedRoutes.length} routes`);
    });

    const handleDeleteJob = (jid) => action("Delete job", async () => {
      await fetchJson(`${API_VRP}/jobs/${jid}`, { method: "DELETE" });
      appendLog(`Job ${jid.substring(0, 12)} deleted`);
      fetchJobList();
    });

    const openJobModal = async (job) => {
      const jid = job.job_id || job.id;
      setJobId(jid);
      try {
        const data = await fetchJson(`${API_VRP}/jobs/${jid}`);
        setSelectedJob(data);
      } catch (_) {
        setSelectedJob(job);
      }
      setShowModal(true);
    };

    const statusColor = (s) => {
      if (s === "completed" || s === "ready") return "var(--success)";
      if (s === "failed") return "var(--danger)";
      if (s === "processing" || s === "pending") return "var(--warning)";
      return "var(--text-muted)";
    };

    return el("div", null,
      el("div", { className: "page-header" },
        el("h2", null, "Job Control"),
        el("p", null, "Monitor, preview, and manage VRP optimization jobs")
      ),

      // Auto-refresh controls
      el("div", { className: "card" },
        el("div", { className: "card-header" },
          el("span", { className: "card-title" }, el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("refresh") } }), "Auto Refresh"),
          el("div", { style: { display: "flex", alignItems: "center", gap: "12px" } },
            el("label", { className: "checkbox-label", style: { textTransform: "none", letterSpacing: 0 } },
              el("input", { type: "checkbox", checked: autoRefresh, onChange: (e) => setAutoRefresh(e.target.checked) }),
              el("span", null, autoRefresh ? "On" : "Off")
            ),
            autoRefresh && el("select", { value: refreshSec, onChange: (e) => setRefreshSec(Number(e.target.value)), style: { width: "auto", fontSize: 12 } },
              el("option", { value: 5 }, "5s"),
              el("option", { value: 10 }, "10s"),
              el("option", { value: 30 }, "30s"),
              el("option", { value: 60 }, "60s")
            ),
            el("button", { className: "btn ghost sm", onClick: fetchJobList },
              el("span", { dangerouslySetInnerHTML: { __html: icon("refresh") } }), " Refresh Now")
          )
        )
      ),

      // Job list
      el("div", { className: "card" },
        el("div", { className: "card-header" },
          el("span", { className: "card-title" }, el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("jobs") } }), "Job List"),
          el("span", { className: "pill accent" }, `${jobList.length}`)
        ),
        jobList.length === 0
          ? el("div", { className: "empty-state" }, el("p", null, "No jobs found. Run an optimization from the Map View page."))
          : el("table", { className: "data-table" },
              el("thead", null,
                el("tr", null,
                  el("th", null, "Job ID"),
                  el("th", null, "Status"),
                  el("th", null, "Created"),
                  el("th", null, "Actions")
                )
              ),
              el("tbody", null,
                jobList.map((j, i) => {
                  const jid = j.job_id || j.id || "";
                  const status = j.status || "unknown";
                  const created = j.created_at ? new Date(j.created_at).toLocaleString() : "-";
                  return el("tr", { key: i, style: { cursor: "pointer" }, onClick: () => openJobModal(j) },
                    el("td", { className: "monospace" }, jid.substring(0, 12) + "..."),
                    el("td", null, el("span", { className: "pill", style: { background: statusColor(status), color: "#fff", fontSize: 11 } }, status)),
                    el("td", null, created),
                    el("td", { onClick: (e) => e.stopPropagation() },
                      el("button", { className: "btn ghost sm", onClick: () => { setJobId(jid); handlePreview(jid); }, title: "Preview" },
                        el("span", { dangerouslySetInnerHTML: { __html: icon("eye") } })),
                      el("button", { className: "btn ghost sm", onClick: () => handleFinalize(jid), title: "Finalize", style: { marginLeft: 4 } },
                        el("span", { dangerouslySetInnerHTML: { __html: icon("check") } })),
                      el("button", { className: "btn ghost sm", onClick: () => { if (confirm("Delete this job?")) handleDeleteJob(jid); }, title: "Delete", style: { marginLeft: 4, color: "#ef4444" } },
                        el("span", { dangerouslySetInnerHTML: { __html: icon("trash") } }))
                    )
                  );
                })
              )
            )
      ),

      // Manual job ID input
      el("div", { className: "card" },
        el("div", { className: "card-header" },
          el("span", { className: "card-title" }, el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("key") } }), "Manual Job Lookup")
        ),
        el("div", { className: "form-grid" },
          el("div", { className: "field span-2" },
            el("label", null, "Job ID"),
            el("input", {
              placeholder: "Enter job UUID...",
              value: jobId,
              onChange: (e) => setJobId(e.target.value),
            })
          )
        ),
        el("div", { className: "actions" },
          el("button", { className: "btn primary", onClick: () => handlePreview(), disabled: loading || !jobId }, el("span", { dangerouslySetInnerHTML: { __html: icon("eye") } }), " Preview"),
          el("button", { className: "btn ghost", onClick: () => handleStatus(), disabled: loading || !jobId }, el("span", { dangerouslySetInnerHTML: { __html: icon("dashboard") } }), " Status"),
          el("button", { className: "btn ghost", onClick: () => handleMetrics(), disabled: loading || !jobId }, el("span", { dangerouslySetInnerHTML: { __html: icon("metrics") } }), " Metrics"),
          el("button", { className: "btn ghost", onClick: handleLoadAll, disabled: loading }, el("span", { dangerouslySetInnerHTML: { __html: icon("box") } }), " Load All"),
          el("button", { className: "btn danger", onClick: () => handleFinalize(), disabled: loading || !jobId }, el("span", { dangerouslySetInnerHTML: { __html: icon("check") } }), " Finalize")
        )
      ),

      // Job output
      el("div", { className: "card" },
        el("div", { className: "card-header" },
          el("span", { className: "card-title" }, el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("clipboard") } }), "Job Output")
        ),
        el("div", { className: "output-box" }, jobResult || "No job selected — enter a Job ID above or click a job in the list")
      ),

      // Job Detail Modal
      showModal && selectedJob && el("div", { className: "modal-overlay", onClick: () => setShowModal(false) },
        el("div", { className: "modal-content", onClick: (e) => e.stopPropagation() },
          el("div", { className: "modal-header" },
            el("h3", null, "Job Details"),
            el("button", { className: "btn ghost sm", onClick: () => setShowModal(false) },
              el("span", { dangerouslySetInnerHTML: { __html: icon("x") } }))
          ),
          el("div", { className: "modal-body" },
            el("div", { className: "form-grid" },
              el("div", { className: "field" },
                el("label", null, "Job ID"),
                el("div", { className: "monospace", style: { fontSize: 13, wordBreak: "break-all" } },
                  selectedJob.job_id || selectedJob.id || "-")
              ),
              el("div", { className: "field" },
                el("label", null, "Status"),
                el("div", null, el("span", { className: "pill",
                  style: { background: statusColor(selectedJob.status), color: "#fff" } },
                  selectedJob.status || "unknown"))
              ),
              selectedJob.created_at && el("div", { className: "field" },
                el("label", null, "Created"),
                el("div", null, new Date(selectedJob.created_at).toLocaleString())
              ),
              selectedJob.completed_at && el("div", { className: "field" },
                el("label", null, "Completed"),
                el("div", null, new Date(selectedJob.completed_at).toLocaleString())
              ),
              selectedJob.error_message && el("div", { className: "field span-2" },
                el("label", null, "Error"),
                el("div", { style: { color: "var(--danger)" } }, selectedJob.error_message)
              ),
              selectedJob.total_routes != null && el("div", { className: "field" },
                el("label", null, "Routes"),
                el("div", null, selectedJob.total_routes)
              ),
              selectedJob.total_tasks != null && el("div", { className: "field" },
                el("label", null, "Tasks"),
                el("div", null, selectedJob.total_tasks)
              )
            ),
            el("div", { className: "output-box", style: { marginTop: 12, maxHeight: 200, overflow: "auto" } },
              JSON.stringify(selectedJob, null, 2))
          ),
          el("div", { className: "modal-footer" },
            el("button", { className: "btn primary", disabled: loading, onClick: () => {
              handlePreview(selectedJob.job_id || selectedJob.id);
              setShowModal(false);
            } }, el("span", { dangerouslySetInnerHTML: { __html: icon("eye") } }), " Preview Routes"),
            el("button", { className: "btn ghost", onClick: () => {
              const jid = selectedJob.job_id || selectedJob.id;
              window.open(`${API_VRP}/jobs/${jid}/preview-page`, "_blank");
            } }, el("span", { dangerouslySetInnerHTML: { __html: icon("scroll") } }), " Open in Tab"),
            el("button", { className: "btn danger", disabled: loading || selectedJob.status === "finalized", onClick: () => {
              handleFinalize(selectedJob.job_id || selectedJob.id);
              setShowModal(false);
            } }, el("span", { dangerouslySetInnerHTML: { __html: icon("check") } }), " Finalize"),
            el("button", { className: "btn ghost", onClick: () => setShowModal(false) }, "Close")
          )
        )
      )
    );
  });

  /* ═══════════════════════════════════════════════════════════════════
     PAGE: Map View (Combined Generator + Map)
     ═══════════════════════════════════════════════════════════════════ */
  const MapViewPage = React.memo(function MapViewPage({ state }) {
    const {
      routes, overview, routeSummaries, showH3, setShowH3,
      animateRoutes, setAnimateRoutes, h3Resolution, tileStatus, setTileStatus,
      setOverview, appendLog, setTaskSummary, setPage, setRoutes, setRouteSummaries, setJobId,
      h3AutoResolution, setH3AutoResolution, h3ResolutionInfo, setH3ResolutionInfo,
      setH3Resolution,
    } = state;

    // ── Map zoom state (declared early for auto H3 resolution) ───────
    const [mapZoom, setMapZoom] = useState(12);

    // ── Generator state ──────────────────────────────────────────────
    const [numJobs, setNumJobs] = useState(1);
    const [numFieldmen, setNumFieldmen] = useState(10);
    const [tasksCi, setTasksCi] = useState(50);
    const [tasksSc, setTasksSc] = useState(30);
    const [tasksDl, setTasksDl] = useState(20);
    const [taskArea, setTaskArea] = useState("AREA_NORTH");
    const [fieldmanAreas, setFieldmanAreas] = useState(["AREA_NORTH"]);
    const [taskBanks, setTaskBanks] = useState("BPI:60,BDO:40");
    const [areaRadius, setAreaRadius] = useState(5);
    const [scatterness, setScatterness] = useState(50);
    const [serviceTime, setServiceTime] = useState(30);
    const [genLoading, setGenLoading] = useState(false);
    const [genResult, setGenResult] = useState("");
    const [customCenter, setCustomCenter] = useState(null);

    // ── Optimizer state ──────────────────────────────────────────────
    const [clusterMode, setClusterMode] = useState("h3");
    const [optAreas, setOptAreas] = useState("AREA_NORTH,AREA_SOUTH,AREA_EAST");
    const [bankQuotaEnabled, setBankQuotaEnabled] = useState(false);
    const [bankQuotaDist, setBankQuotaDist] = useState("BPI:60,BDO:40");
    const [optServiceTime, setOptServiceTime] = useState(30);
    const [maxRadius, setMaxRadius] = useState(5);
    const [nearbyFilter, setNearbyFilter] = useState(false);
    const [showRadiusCircles, setShowRadiusCircles] = useState(false);
    const [optLoading, setOptLoading] = useState(false);
    const [optResult, setOptResult] = useState("");
    const [optPriorityMin, setOptPriorityMin] = useState("");
    const [optPriorityMax, setOptPriorityMax] = useState("");

    // ── Map state ────────────────────────────────────────────────────
    const mapContainerRef = useRef(null);
    const [mapReady, setMapReady] = useState(false);

    const mapRef = useLeafletMap(
      mapContainerRef,
      () => setMapReady(true),
      (s) => setTileStatus(s)
    );

    // ── Panel collapse state ─────────────────────────────────────────
    const [panelCollapsed, setPanelCollapsed] = useState(false);

    // ── Picker mode state ────────────────────────────────────────────
    // pickerMode: null | "task" | "fieldman"
    const [pickerMode, setPickerMode] = useState(null);
    const [pickedItems, setPickedItems] = useState([]);       // Array of PickerItemResponse
    const pickerMarkersRef = useRef([]);                       // Leaflet markers for picked items
    const [pickerSaving, setPickerSaving] = useState(false);
    const [pickerTaskType, setPickerTaskType] = useState("credit_investigation");
    const [pickerBank, setPickerBank] = useState("");
    const [pickerFieldmanArea, setPickerFieldmanArea] = useState("AREA_NORTH");

    // ── Priority popup state (shown on task placement) ───────────────
    const [priorityPopup, setPriorityPopup] = useState(null); // { lat, lng } or null
    const [priorityInput, setPriorityInput] = useState("");
    const [editPriorityTask, setEditPriorityTask] = useState(null); // { task_id, current } or null
    const [editPriorityInput, setEditPriorityInput] = useState("");

    // ── Map data filter ──────────────────────────────────────────────
    const [mapDataFilter, setMapDataFilter] = useState("all"); // "all" | "tasks" | "fieldmen"

    // ── FM Location Update state ─────────────────────────────────────
    const [moveFmId, setMoveFmId] = useState("");
    const [fmLocPickerActive, setFmLocPickerActive] = useState(false);
    const [fmLocLat, setFmLocLat] = useState("");
    const [fmLocLng, setFmLocLng] = useState("");

    // ── Generator handlers ───────────────────────────────────────────
    const handleRandomize = async () => {
      setGenLoading(true);
      try {
        const payload = {
          num_fieldmen: Number(numFieldmen),
          num_jobs: Number(numJobs),
          tasks_ci: Number(tasksCi),
          tasks_sc: Number(tasksSc),
          tasks_dl: Number(tasksDl),
          task_area: taskArea,
          fieldman_areas: fieldmanAreas,
          task_banks: taskBanks || null,
          area_radius_km: Number(areaRadius),
          scatterness: Number(scatterness),
          service_time_minutes: Number(serviceTime),
        };
        if (taskArea === "CUSTOM" && customCenter) {
          payload.center_lat = customCenter.lat;
          payload.center_lng = customCenter.lng;
        }
        const data = await fetchJson(`${API_VRP}/randomize`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (data.overview) {
          setOverview(data.overview);
        } else {
          // Large batch — fetch overview separately
          fetchJson(`${API_VRP}/overview`).then(o => { if (o) setOverview(o); }).catch(() => {});
        }
        const jobsLabel = data.jobs_generated > 1 ? ` (${data.jobs_generated} jobs auto-queued)` : "";
        setGenResult(`✓ Created ${data.tasks_created} tasks, ${data.fieldmen_created} fieldmen${jobsLabel}`);
        appendLog(`Randomized: ${data.tasks_created} tasks, ${data.fieldmen_created} fieldmen${jobsLabel}`);
        if (data.jobs_generated > 1 && state.bumpJobList) state.bumpJobList();
        invalidateCache(); // Bust all caches after data mutation
        Object.keys(_h3Cache).forEach(k => delete _h3Cache[k]); // Bust H3 client cache
        refreshSummary();
        appendLog("Geocoding addresses in background (Nominatim)...");
        fetchJson(`${API_VRP}/geocode-addresses`, { method: "POST" })
          .then(g => {
            appendLog(`Geocoded: ${g.updated}/${g.total} addresses updated`);
            invalidateCache("overview");
            fetchJson(`${API_VRP}/overview`).then(o => setOverview(o)).catch(() => {});
          })
          .catch(e => appendLog(`Geocode warning: ${e.message}`));
      } catch (err) {
        setGenResult(err.message);
        appendLog(`Randomize failed: ${err.message}`);
        showToast(`Randomize failed: ${err.message}`, 'error');
      } finally { setGenLoading(false); }
    };

    const handleReset = async () => {
      setGenLoading(true);
      try {
        await fetchJson(`${API_VRP}/data/reset`, { method: "POST" });
        invalidateCache(); // Bust all caches after reset
        Object.keys(_h3Cache).forEach(k => delete _h3Cache[k]); // Bust H3 client cache
        setOverview({ tasks: [], fieldmen: [] });
        setRoutes([]);
        setRouteSummaries([]);
        setGenResult("All data cleared.");
        appendLog("All data reset");
        showToast('All data cleared', 'success');
        setTaskSummary({ total_tasks: 0, total_fieldmen: 0, by_type: {}, by_bank: {} });
      } catch (err) {
        setGenResult(err.message);
        appendLog(`Reset failed: ${err.message}`);
        showToast(`Reset failed: ${err.message}`, 'error');
      } finally { setGenLoading(false); }
    };

    const refreshSummary = async () => {
      try {
        const data = await fetchJson(`${API_VRP}/task-summary`);
        setTaskSummary(data);
      } catch (_) {}
    };

    const handleOptimize = async () => {
      setOptLoading(true);
      try {
        const data = await fetchJson(`${API_VRP}/optimize`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            assignment_strategy: clusterMode,
            h3_resolution: Number(h3Resolution) || 9,
            h3_auto_resolution: h3AutoResolution,
            areas: optAreas || null,
            bank_quota_enabled: bankQuotaEnabled,
            bank_quota_distribution: bankQuotaEnabled ? bankQuotaDist : null,
            service_time_minutes: Number(optServiceTime),
            max_radius_km: Number(maxRadius),
            nearby_filter: nearbyFilter,
            ...(optPriorityMin ? { priority_min: Number(optPriorityMin) } : {}),
            ...(optPriorityMax ? { priority_max: Number(optPriorityMax) } : {}),
          }),
        });
        const jid = data.job_id;
        if (jid) setJobId(jid);
        setOptResult(`Job ${jid} created — polling for completion...`);
        appendLog(`Optimization job created: ${jid}`);
        invalidateCache("jobs"); // Bust job caches
        if (jid) {
          let attempts = 0;
          const maxAttempts = 120;
          const poll = async () => {
            while (attempts < maxAttempts) {
              attempts++;
              await new Promise((r) => setTimeout(r, 1000));
              try {
                const status = await fetchJson(`${API_VRP}/jobs/${jid}`);
                setOptResult(`Job ${jid}: ${status.status || "processing"} (${attempts}s)`);
                if (status.status === "completed" || status.status === "ready") {
                  appendLog(`Job ${jid} completed — loading routes...`);
                  invalidateCache(); // Bust all caches on job completion
                  // Clear H3 client cache so grid refreshes with new data
                  Object.keys(_h3Cache).forEach(k => delete _h3Cache[k]);
                  // Extract and display resolution info
                  if (status.h3_resolution_info) {
                    const ri = status.h3_resolution_info;
                    setH3ResolutionInfo(ri);
                    if (ri.mode === "auto") {
                      setH3Resolution(ri.resolution);
                      appendLog(`H3 auto-detected resolution: ${ri.resolution} (bbox: ${ri.bbox_diagonal_km}km)`);
                    } else {
                      appendLog(`H3 manual resolution: ${ri.resolution} (auto would suggest: ${ri.auto_suggested})`);
                    }
                  }
                  try {
                    const preview = await fetchJson(`${API_VRP}/jobs/${jid}/preview`);
                    const loadedRoutes = preview.routes || [];
                    setRoutes(loadedRoutes);
                    setRouteSummaries(
                      loadedRoutes.map((r) => ({
                        fieldman_id: r.fieldman_id, distance: r.distance, duration: r.duration, tasks: r.tasks.length,
                      }))
                    );
                    setOptResult(`Done! ${loadedRoutes.length} routes loaded.`);
                    appendLog(`Loaded ${loadedRoutes.length} routes from job ${jid}`);
                    showToast(`Optimization complete: ${loadedRoutes.length} routes`, 'success');
                  } catch (previewErr) {
                    const allData = await fetchJson(`${API_VRP}/assignments?include_geometry=true&include_overview=true`);
                    const loadedRoutes = (allData.routes || []).map((r) => {
                      const geom = r.geometry;
                      let coords = [];
                      if (Array.isArray(geom)) coords = geom;
                      else if (geom && Array.isArray(geom.coordinates)) coords = geom.coordinates;
                      return { ...r, geometry: { type: "LineString", coordinates: coords } };
                    });
                    setRoutes(loadedRoutes);
                    setOverview(allData.overview || { tasks: [], fieldmen: [] });
                    setRouteSummaries(
                      loadedRoutes.map((r) => ({
                        fieldman_id: r.fieldman_id,
                        distance: r.tasks.reduce((s, t) => s + (t.distance || 0), 0),
                        duration: r.tasks.reduce((s, t) => s + (t.duration || 0), 0),
                        tasks: r.tasks.length,
                      }))
                    );
                    setOptResult(`Done! ${loadedRoutes.length} routes loaded (via assignments).`);
                    appendLog(`Loaded ${loadedRoutes.length} routes via assignments`);
                  }
                  return;
                }
                if (status.status === "failed") {
                  setOptResult(`Job ${jid} FAILED: ${status.error_message || "Unknown error"}`);
                  appendLog(`Job ${jid} failed`);
                  showToast(`Job ${jid} failed: ${status.error_message || 'Unknown error'}`, 'error');
                  return;
                }
              } catch (pollErr) {
                setOptResult(`Polling error: ${pollErr.message}`);
              }
            }
            setOptResult(`Job ${jid} still processing after ${maxAttempts}s. Check Jobs page.`);
          };
          await poll();
        }
      } catch (err) {
        setOptResult(err.message);
        appendLog(`Optimize failed: ${err.message}`);
        showToast(`Optimize failed: ${err.message}`, 'error');
      } finally { setOptLoading(false); }
    };

    // ── Auto-recompute routing when H3 resolution changes (manual only) ─
    const prevH3Res = useRef(h3Resolution);
    useEffect(() => {
      if (prevH3Res.current === h3Resolution) return;
      prevH3Res.current = h3Resolution;
      // Skip auto-recompute when auto-resolution is active (backend already set the right value)
      if (h3AutoResolution) return;
      // Only auto-recompute if routes already exist (user has optimized before)
      if (!routes || routes.length === 0 || optLoading) return;
      appendLog(`H3 resolution changed to ${h3Resolution} — auto-recomputing routes...`);
      const timer = setTimeout(() => {
        handleOptimize();
      }, 800); // debounce 800ms
      return () => clearTimeout(timer);
    }, [h3Resolution]);

    const handleExport = async () => {
      try {
        const ov = await fetchJson(`${API_VRP}/overview`);
        const blob = new Blob([JSON.stringify(ov, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url; a.download = "vrp-data.json"; a.click();
        URL.revokeObjectURL(url);
        appendLog("Data exported as JSON");
      } catch (err) { appendLog(`Export failed: ${err.message}`); }
    };

    const handleImport = () => {
      const input = document.createElement("input");
      input.type = "file"; input.accept = ".json";
      input.onchange = async (e) => {
        const file = e.target.files[0]; if (!file) return;
        try {
          const text = await file.text();
          const data = JSON.parse(text);
          setOptResult(`Imported: ${JSON.stringify(data).substring(0, 200)}...`);
          appendLog("JSON file imported");
        } catch (err) { appendLog(`Import failed: ${err.message}`); }
      };
      input.click();
    };

    useEffect(() => { refreshSummary(); }, []);

    const toggleFieldmanArea = (area) => {
      setFieldmanAreas((prev) =>
        prev.includes(area) ? prev.filter((a) => a !== area) : [...prev, area]
      );
    };

    const allAreas = ["AREA_NORTH", "AREA_SOUTH", "AREA_EAST", "CUSTOM"];

    // Custom center via direct map click — picker mode for area center
    const handleMapClickForCenter = () => {
      if (!mapRef.current) {
        const input = prompt("Enter center coordinates (lat, lng):", "14.715, 121.0");
        if (input) {
          const [lat, lng] = input.split(",").map(Number);
          if (!isNaN(lat) && !isNaN(lng)) {
            setCustomCenter({ lat, lng }); setTaskArea("CUSTOM");
            appendLog(`Custom center set: ${lat.toFixed(5)}, ${lng.toFixed(5)}`);
          }
        }
        return;
      }
      // Block if picker mode is active
      if (pickerMode) {
        appendLog("End picker mode first before setting area center");
        return;
      }
      const { map } = mapRef.current;
      const container = map.getContainer();
      container.classList.add("picker-active");
      appendLog("Click on the map to set custom area center...");

      // Disable pointer events on overlay panes so clicks pass through to the map
      const panes = ["markerPane", "overlayPane", "shadowPane", "tooltipPane", "popupPane"];
      const savedStyles = {};
      panes.forEach(p => {
        const pane = map.getPane(p);
        if (pane) { savedStyles[p] = pane.style.pointerEvents; pane.style.pointerEvents = "none"; }
      });

      const restorePanes = () => {
        panes.forEach(p => {
          const pane = map.getPane(p);
          if (pane) pane.style.pointerEvents = savedStyles[p] || "";
        });
      };

      const centerHandler = (e) => {
        const { lat, lng } = e.latlng;
        setCustomCenter({ lat, lng });
        setTaskArea("CUSTOM");
        container.classList.remove("picker-active");
        restorePanes();
        document.removeEventListener("keydown", centerEsc);
        appendLog(`Custom center set: ${lat.toFixed(5)}, ${lng.toFixed(5)}`);
      };
      map.once("click", centerHandler);
      const centerEsc = (ev) => {
        if (ev.key === "Escape") {
          container.classList.remove("picker-active");
          restorePanes();
          map.off("click", centerHandler);
          document.removeEventListener("keydown", centerEsc);
          appendLog("Center pick cancelled");
        }
      };
      document.addEventListener("keydown", centerEsc);
    };

    // ── Picker mode handlers (Task / Fieldman) ──────────────────────
    const startPicker = (mode) => {
      if (pickerMode === mode) { endPicker(); return; } // Toggle off
      setPickerMode(mode);
      const labels = { task: "Task", fieldman: "Fieldman", move_fm: "Move Fieldman" };
      const hint = mode === "move_fm"
        ? "Select a fieldman below, then click on the map to set their new location"
        : "click on the map to place points";
      appendLog(`Picker mode: ${labels[mode] || mode} — ${hint}`);
    };

    const endPicker = () => {
      setPickerMode(null);
      setPriorityPopup(null);
      if (mapRef.current) mapRef.current.map.getContainer().classList.remove("picker-active");
      appendLog("Picker mode ended");
    };

    // ── Confirm task placement (called from priority popup) ──────────
    const confirmPlaceTask = async (manualPriority) => {
      if (!priorityPopup) return;
      const { lat, lng } = priorityPopup;
      setPriorityPopup(null);
      setPickerSaving(true);
      try {
        const body = {
          latitude: lat, longitude: lng,
          task_type: pickerTaskType,
          bank: pickerBank || null,
          service_time_minutes: Number(serviceTime),
        };
        if (manualPriority !== null && manualPriority !== undefined) {
          body.manual_priority = Number(manualPriority);
        }
        const result = await fetchJson(`${API_VRP}/picker/task`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        setPickedItems(prev => [...prev, result]);
        const prioLabel = manualPriority != null ? ` [Priority: ${manualPriority}]` : "";
        appendLog(`Placed task: ${result.address || `${lat.toFixed(5)}, ${lng.toFixed(5)}`}${prioLabel}`);
        invalidateCache("overview");
        invalidateCache("task-summary");
        addPickerMarkerToMap(result);
        try { const ov = await fetchJson(`${API_VRP}/overview`); setOverview(ov); } catch (_) {}
      } catch (err) {
        appendLog(`Picker error: ${err.message}`);
      } finally {
        setPickerSaving(false);
      }
    };

    // ── Edit task priority (called from map popup or picked items) ──
    const handleSavePriority = async () => {
      if (!editPriorityTask) return;
      const val = editPriorityInput.trim() === "" ? null : Number(editPriorityInput);
      if (val !== null && (isNaN(val) || val < 0 || val > 100)) {
        appendLog("Priority must be a float between 0.0 and 100.0, or empty to clear");
        return;
      }
      try {
        await fetchJson(`${API_VRP}/picker/task/${editPriorityTask.task_id}/priority`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ manual_priority: val }),
        });
        appendLog(`Priority updated for ${editPriorityTask.task_id.substring(0, 8)}: ${val ?? "default"}`);
        // Update picked items list
        setPickedItems(prev => prev.map(item =>
          item.id === editPriorityTask.task_id ? { ...item, manual_priority: val } : item
        ));
        setEditPriorityTask(null);
        invalidateCache("overview");
        try { const ov = await fetchJson(`${API_VRP}/overview`); setOverview(ov); } catch (_) {}
      } catch (err) {
        appendLog(`Priority update failed: ${err.message}`);
      }
    };

    const removePickedItem = async (item, index) => {
      try {
        const endpoint = item.type === "task" ? "task" : "fieldman";
        await fetchJson(`${API_VRP}/picker/${endpoint}/${item.id}`, { method: "DELETE" });
        setPickedItems(prev => prev.filter((_, i) => i !== index));
        // Remove marker from map
        if (pickerMarkersRef.current[index] && mapRef.current) {
          mapRef.current.map.removeLayer(pickerMarkersRef.current[index]);
          pickerMarkersRef.current.splice(index, 1);
        }
        appendLog(`Removed picked ${item.type}: ${item.address || item.id.substring(0, 8)}`);
        invalidateCache("overview");
        invalidateCache("task-summary");
        // Refresh overview to update map
        try {
          const ov = await fetchJson(`${API_VRP}/overview`);
          setOverview(ov);
        } catch (_) {}
      } catch (err) {
        appendLog(`Delete failed: ${err.message}`);
      }
    };

    // Picker mode effect — manage crosshair cursor + click handler
    useEffect(() => {
      if (!mapReady || !mapRef.current) return;
      const { map } = mapRef.current;
      const container = map.getContainer();

      if (!pickerMode) {
        container.classList.remove("picker-active");
        return;
      }

      container.classList.add("picker-active");

      const handleClick = async (e) => {
        const { lat, lng } = e.latlng;
        if (pickerMode === "task") {
          // Show priority popup instead of immediately creating
          setPriorityPopup({ lat, lng });
          setPriorityInput("");
          return;
        }
        setPickerSaving(true);
        try {
          let result;
          if (pickerMode === "move_fm") {
            if (!moveFmId) {
              appendLog("Select a fieldman to move first");
              setPickerSaving(false);
              return;
            }
            result = await fetchJson(`${API_VRP}/fieldman/${moveFmId}/location`, {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ latitude: lat, longitude: lng }),
            });
          } else {
            result = await fetchJson(`${API_VRP}/picker/fieldman`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                latitude: lat, longitude: lng,
                area: pickerFieldmanArea,
              }),
            });
          }

          invalidateCache("overview");
          invalidateCache("task-summary");

          if (pickerMode === "move_fm") {
            // Move: update route FM positions in-place (no new marker)
            setRoutes(prev => prev.map(r =>
              r.fieldman_id === moveFmId ? { ...r, start_lat: lat, start_long: lng } : r
            ));
          } else {
            // New fieldman: add picker marker + tracked item
            setPickedItems(prev => [...prev, result]);
            addPickerMarkerToMap(result);
          }
          appendLog(pickerMode === "move_fm"
            ? `Fieldman ${moveFmId.substring(0, 8)} moved to ${lat.toFixed(5)}, ${lng.toFixed(5)}`
            : `Placed ${result.type}: ${result.address || `${lat.toFixed(5)}, ${lng.toFixed(5)}`}`);

          // Refresh overview so overview layer updates
          try {
            const ov = await fetchJson(`${API_VRP}/overview`);
            setOverview(ov);
          } catch (_) {}
        } catch (err) {
          appendLog(`Picker error: ${err.message}`);
        } finally {
          setPickerSaving(false);
        }
      };

      const handleKeydown = (ev) => {
        if (ev.key === "Escape") {
          endPicker();
        }
      };

      map.on("click", handleClick);
      document.addEventListener("keydown", handleKeydown);

      return () => {
        map.off("click", handleClick);
        document.removeEventListener("keydown", handleKeydown);
        container.classList.remove("picker-active");
      };
    }, [pickerMode, mapReady, pickerTaskType, pickerBank, pickerFieldmanArea, serviceTime, moveFmId]);

    // ── FM Location picker — one-shot click to fill lat/lng ──────────
    useEffect(() => {
      if (!fmLocPickerActive || !mapReady || !mapRef.current) return;
      const { map } = mapRef.current;
      const container = map.getContainer();
      container.classList.add("picker-active");

      const handleClick = (e) => {
        const { lat, lng } = e.latlng;
        setFmLocLat(lat.toFixed(6));
        setFmLocLng(lng.toFixed(6));
        setFmLocPickerActive(false);
        appendLog(`Location picked: ${lat.toFixed(5)}, ${lng.toFixed(5)} — click Update Location to save`);
      };
      const handleEsc = (ev) => {
        if (ev.key === "Escape") setFmLocPickerActive(false);
      };
      map.on("click", handleClick);
      document.addEventListener("keydown", handleEsc);

      return () => {
        map.off("click", handleClick);
        document.removeEventListener("keydown", handleEsc);
        container.classList.remove("picker-active");
      };
    }, [fmLocPickerActive, mapReady]);

    // Helper: add a picked marker to the map
    const addPickerMarkerToMap = (item) => {
      if (!mapRef.current) return;
      const { map } = mapRef.current;
      let markerIcon;
      const hasPriority = item.type === "task" && item.manual_priority != null;
      if (item.type === "task") {
        const color = TASK_TYPE_COLORS[item.task_type] || "#6366f1";
        const prioBadge = hasPriority
          ? `<div style="position:absolute;top:-8px;left:-8px;background:#f59e0b;color:#fff;border:1.5px solid #fff;border-radius:8px;padding:0 4px;height:16px;display:flex;align-items:center;justify-content:center;font-size:8px;font-weight:800;box-shadow:0 1px 3px rgba(0,0,0,.3);white-space:nowrap;">★${item.manual_priority}</div>`
          : "";
        markerIcon = L.divIcon({
          className: "vrp-picker-pin",
          html: `<div style="position:relative;">
            <svg xmlns="http://www.w3.org/2000/svg" width="28" height="40" viewBox="0 0 28 40">
              <defs><filter id="pds${pickerMarkersRef.current.length}" x="-20%" y="-10%" width="140%" height="130%"><feDropShadow dx="0" dy="1.5" stdDeviation="1.5" flood-opacity="0.3"/></filter></defs>
              <path d="M14 0C6.27 0 0 6.27 0 14c0 10.5 14 26 14 26s14-15.5 14-26C28 6.27 21.73 0 14 0z" fill="${color}" filter="url(#pds${pickerMarkersRef.current.length})"/>
              <circle cx="14" cy="12" r="6" fill="#fff"/>
              <circle cx="14" cy="12" r="2.5" fill="${color}"/>
            </svg>
            <div style="position:absolute;top:-4px;right:-6px;background:#fff;color:${color};border:1.5px solid ${color};border-radius:50%;width:16px;height:16px;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:800;box-shadow:0 1px 3px rgba(0,0,0,.2);">✦</div>
            ${prioBadge}
          </div>`,
          iconSize: [28, 40],
          iconAnchor: [14, 40],
        });
      } else {
        markerIcon = L.divIcon({
          className: "vrp-picker-pin",
          html: `<div style="position:relative;">
            <svg xmlns="http://www.w3.org/2000/svg" width="28" height="40" viewBox="0 0 28 40">
              <defs><filter id="pfds${pickerMarkersRef.current.length}" x="-20%" y="-10%" width="140%" height="130%"><feDropShadow dx="0" dy="1.5" stdDeviation="1.5" flood-opacity="0.3"/></filter></defs>
              <path d="M14 0C6.27 0 0 6.27 0 14c0 10.5 14 26 14 26s14-15.5 14-26C28 6.27 21.73 0 14 0z" fill="#ef4444" filter="url(#pfds${pickerMarkersRef.current.length})"/>
              <circle cx="14" cy="12" r="6" fill="#fff"/>
              <svg x="8" y="6" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="5"/><path d="M20 21a8 8 0 1 0-16 0"/></svg>
            </svg>
            <div style="position:absolute;top:-4px;right:-6px;background:#fff;color:#ef4444;border:1.5px solid #ef4444;border-radius:50%;width:16px;height:16px;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:800;box-shadow:0 1px 3px rgba(0,0,0,.2);">FM</div>
          </div>`,
          iconSize: [28, 40],
          iconAnchor: [14, 40],
        });
      }
      const prioInfo = hasPriority ? ` | Priority: ${item.manual_priority}` : "";
      const marker = L.marker([item.latitude, item.longitude], { icon: markerIcon, zIndexOffset: 900 })
        .bindTooltip(`${item.type === "task" ? "Task" : "Fieldman"}: ${item.address || item.id.substring(0, 8)}${prioInfo}`, { permanent: false })
        .addTo(map);
      pickerMarkersRef.current.push(marker);
    };

    // Dropped pin marker for custom center (separate from picker)
    const customCenterMarkerRef = useRef(null);
    useEffect(() => {
      if (!mapReady || !mapRef.current) return;
      if (customCenterMarkerRef.current) {
        mapRef.current.map.removeLayer(customCenterMarkerRef.current);
        customCenterMarkerRef.current = null;
      }
      if (!customCenter) return;

      const pinSvg = `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="44" viewBox="0 0 32 44">
        <defs><filter id="ds" x="-20%" y="-10%" width="140%" height="130%"><feDropShadow dx="0" dy="2" stdDeviation="2" flood-opacity="0.3"/></filter></defs>
        <path d="M16 0C7.16 0 0 7.16 0 16c0 12 16 28 16 28s16-16 16-28C32 7.16 24.84 0 16 0z" fill="#6366f1" filter="url(#ds)"/>
        <circle cx="16" cy="14" r="7" fill="#fff"/>
        <circle cx="16" cy="14" r="3" fill="#6366f1"/>
      </svg>`;

      const pinIcon = L.divIcon({
        className: "vrp-picker-pin",
        html: pinSvg,
        iconSize: [32, 44],
        iconAnchor: [16, 44],
        popupAnchor: [0, -44],
      });

      const marker = L.marker([customCenter.lat, customCenter.lng], { icon: pinIcon, zIndexOffset: 1000 })
        .bindTooltip(`Area center: ${customCenter.lat.toFixed(5)}, ${customCenter.lng.toFixed(5)}`, { permanent: false })
        .addTo(mapRef.current.map);

      customCenterMarkerRef.current = marker;
    }, [customCenter, mapReady]);

    // ── Picker-filtered overview: STRICT mode isolation at data level ──
    const drawOverview = useMemo(() => {
      let tasks = overview.tasks;
      let fieldmen = overview.fieldmen;

      // When routes are loaded from a specific job, hide tasks/fieldmen
      // already shown as route markers to avoid visual overlap with other batches
      if (routes.length > 0) {
        const routeTaskIds = new Set();
        const routeFmIds = new Set();
        routes.forEach(r => {
          if (r.fieldman_id) routeFmIds.add(r.fieldman_id);
          (r.tasks || []).forEach(t => { if (t.task_id) routeTaskIds.add(t.task_id); });
        });
        // Show only tasks/fieldmen NOT in the current route set
        if (routeTaskIds.size > 0) tasks = tasks.filter(t => !routeTaskIds.has(t.task_id));
        if (routeFmIds.size > 0) fieldmen = fieldmen.filter(f => !routeFmIds.has(f.fieldman_id));
      }

      // Apply data type filter
      if (mapDataFilter === "tasks") fieldmen = [];
      else if (mapDataFilter === "fieldmen") tasks = [];

      // Picker mode overrides
      if (pickerMode === "task") fieldmen = [];
      else if (pickerMode === "fieldman" || pickerMode === "move_fm") tasks = [];

      return { tasks, fieldmen };
    }, [overview, pickerMode, mapDataFilter, routes]);

    // Also filter routes for picker mode
    const drawRoutes = useMemo(() => {
      return routes; // Routes always shown (they contain both tasks + fieldmen in each route)
    }, [routes]);

    // ── Map drawing: computed points ─────────────────────────────────
    const mapPoints = useMemo(() => {
      const pts = [];
      drawOverview.tasks.forEach((t) => pts.push({ lat: t.latitude, lng: t.longitude }));
      drawOverview.fieldmen.forEach((f) => pts.push({ lat: f.latitude, lng: f.longitude }));
      drawRoutes.forEach((r) => {
        r.tasks.forEach((t) => pts.push({ lat: t.latitude, lng: t.longitude }));
        if (r.start_lat != null && r.start_long != null) pts.push({ lat: r.start_lat, lng: r.start_long });
      });
      return pts;
    }, [drawOverview, drawRoutes]);

    // ── Auto H3 resolution: recalc when toggled on, data changes, or zoom changes ──
    useEffect(() => {
      if (!h3AutoResolution) return;
      if (!mapPoints || mapPoints.length === 0) return;
      const suggested = suggestH3Resolution(mapPoints, mapZoom);
      if (suggested !== h3Resolution) {
        setH3Resolution(suggested);
        // Clear H3 client cache so grid re-renders immediately
        Object.keys(_h3Cache).forEach(k => delete _h3Cache[k]);
      }
    }, [h3AutoResolution, mapPoints, mapZoom]);

    // ── Draw routes + markers (uses pre-filtered drawOverview) ─────
    useEffect(() => {
      if (!mapReady || !mapRef.current) return;
      const { map, layers } = mapRef.current;
      const colored = drawRoutes.map((r, i) => ({ ...r, color: PALETTE[i % PALETTE.length] }));

      // Clear ALL marker layers
      layers.routes.clearLayers();
      layers.tasks.clearLayers();
      layers.fieldmen.clearLayers();
      layers.overviewTasks.clearLayers();
      layers.overviewFieldmen.clearLayers();

      // Ensure all layer groups are on the map
      if (!map.hasLayer(layers.routes)) map.addLayer(layers.routes);
      if (!map.hasLayer(layers.tasks)) map.addLayer(layers.tasks);
      if (!map.hasLayer(layers.fieldmen)) map.addLayer(layers.fieldmen);
      if (!map.hasLayer(layers.overviewTasks)) map.addLayer(layers.overviewTasks);
      if (!map.hasLayer(layers.overviewFieldmen)) map.addLayer(layers.overviewFieldmen);

      colored.forEach((route) => {
        if (route.geometry && route.geometry.type === "LineString") {
          const rawCoords = Array.isArray(route.geometry.coordinates) ? route.geometry.coordinates : [];
          let coords = rawCoords.map((c) => [c[1], c[0]]);
          if (coords.length > 1) {
            L.polyline(coords, {
              color: route.color, weight: 3, opacity: 0.85, lineCap: "round", lineJoin: "round",
              dashArray: animateRoutes ? "6 10" : null,
              className: "route-line",
            }).addTo(layers.routes);
          }
        }
        // Route assigned tasks — only when NOT in fieldman picker mode
        if (pickerMode !== "fieldman") {
          route.tasks.forEach((t, idx) => {
            const seq = idx + 1;
            const isTaskDone = t.status === "completed";
            const markerBg = isTaskDone ? "#22c55e" : route.color;
            const markerLabel = isTaskDone ? "✓" : seq;
            const markerOpacity = isTaskDone ? "0.7" : "1";
            const taskIcon = L.divIcon({
              className: "vrp-task-marker",
              html: `<div style="background:${markerBg};color:#fff;border:2px solid #fff;border-radius:50%;width:24px;height:24px;display:flex;align-items:center;justify-content:center;font-size:${isTaskDone ? "14" : "11"}px;font-weight:700;box-shadow:0 1px 4px rgba(0,0,0,0.3);opacity:${markerOpacity};">${markerLabel}</div>`,
              iconSize: [24, 24],
              iconAnchor: [12, 12],
            });
            L.marker([t.latitude, t.longitude], { icon: taskIcon })
              .bindTooltip(buildTaskTooltip(t, seq, route.color), { direction: "auto", offset: [0, -14], className: "preview-tt" }).addTo(layers.tasks);
          });
        }
        // Route fieldman markers — only when NOT in task picker mode
        // If tasks have been completed, show FM at last completed task location
        if (pickerMode !== "task") {
          const doneTasks = (route.tasks || []).filter(t => t.status === "completed" && t.latitude != null);
          const lastDone = doneTasks.length > 0 ? doneTasks[doneTasks.length - 1] : null;
          const fmLat = lastDone ? lastDone.latitude : route.start_lat;
          const fmLng = lastDone ? lastDone.longitude : route.start_long;
          const fmTTExtra = lastDone ? `, at task #${lastDone.sequence || "?"}` : "";

          if (fmLat != null && fmLng != null) {
            const fmIcon = L.divIcon({
              className: "vrp-fieldman-marker",
              html: `<div style="background:#ef4444;color:#fff;border:2px solid #fff;border-radius:50%;width:30px;height:30px;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 6px rgba(0,0,0,0.35);"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="4" cy="19" r="3"/><circle cx="19" cy="19" r="3"/><path d="M12 19V9l-3 3"/><path d="M9 12h6l3-6h-4l-2-3H7"/></svg></div>`,
              iconSize: [30, 30],
              iconAnchor: [15, 15],
            });
            L.marker([fmLat, fmLng], { icon: fmIcon })
              .bindTooltip(buildFieldmanTooltip(route.fieldman_id, route.color, { tasks: route.tasks.length, distance: route.distance, duration: route.duration }) + (fmTTExtra ? `<div class="tt-row"><span class="tt-label">Current</span><span class="tt-value" style="color:#22c55e">${lastDone.address || "Last done"}${fmTTExtra}</span></div>` : ""), { direction: "auto", offset: [0, -18], className: "preview-tt" }).addTo(layers.fieldmen);
          }

          // Show home as small dashed marker when FM has moved
          if (lastDone && route.start_lat != null && route.start_long != null) {
            const homeIcon = L.divIcon({
              className: "vrp-fieldman-home",
              html: `<div style="background:rgba(239,68,68,0.3);color:#ef4444;border:2px dashed #ef4444;border-radius:50%;width:20px;height:20px;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:700;">H</div>`,
              iconSize: [20, 20],
              iconAnchor: [10, 10],
            });
            L.marker([route.start_lat, route.start_long], { icon: homeIcon, zIndexOffset: 400 })
              .bindTooltip(`Home base: FM ${route.fieldman_id.substring(0, 8)}`, { direction: "auto", offset: [0, -12], className: "preview-tt" })
              .addTo(layers.fieldmen);
          }
        }
      });

      // Overview markers — viewport-culled for performance with large datasets
      let _prevBoundsKey = "";
      const _renderOverviewMarkers = () => {
        // Quick bounds-change check to skip redundant redraws
        const bounds = map.getBounds().pad(0.2);  // 20% buffer beyond viewport
        const bKey = `${bounds.getSouth().toFixed(3)},${bounds.getWest().toFixed(3)},${bounds.getNorth().toFixed(3)},${bounds.getEast().toFixed(3)}`;
        if (bKey === _prevBoundsKey) return; // Skip if bounds haven't changed meaningfully
        _prevBoundsKey = bKey;

        layers.overviewTasks.clearLayers();
        layers.overviewFieldmen.clearLayers();
        const south = bounds.getSouth(), north = bounds.getNorth(), west = bounds.getWest(), east = bounds.getEast();
        // Fast inline bounds check (avoids LatLng object creation per point)
        const visibleTasks = [];
        for (let i = 0, len = drawOverview.tasks.length; i < len; i++) {
          const t = drawOverview.tasks[i];
          if (t.latitude >= south && t.latitude <= north && t.longitude >= west && t.longitude <= east) {
            visibleTasks.push(t);
            if (visibleTasks.length >= 4000) break; // Hard cap
          }
        }
        // Batch-add circle markers (canvas-rendered, minimal overhead)
        for (let i = 0, len = visibleTasks.length; i < len; i++) {
          const t = visibleTasks[i];
          const taskColor = TASK_TYPE_COLORS[t.task_type] || "#6366f1";
          const hasMPrio = t.manual_priority != null;
          const cm = L.circleMarker([t.latitude, t.longitude], {
            radius: hasMPrio ? 8 : 6, color: hasMPrio ? "#f59e0b" : "#fff", weight: hasMPrio ? 2 : 1.5, fillColor: taskColor, fillOpacity: 0.85,
          }).addTo(layers.overviewTasks);
          cm.on("mouseover", function() {
            if (!this.getTooltip()) {
              this.bindTooltip(buildTaskTooltip(t, "", taskColor), { direction: "auto", offset: [0, -10], className: "preview-tt" });
            }
            this.openTooltip();
          });
        }
        // Fieldmen as divIcon markers (few enough for DOM, shows icon logo)
        const visFm = drawOverview.fieldmen;
        for (let i = 0, len = visFm.length; i < len; i++) {
          const f = visFm[i];
          const fmIcon = L.divIcon({
            className: "vrp-fieldman-marker",
            html: `<div style="background:#ef4444;color:#fff;border:2px solid #fff;border-radius:50%;width:28px;height:28px;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 6px rgba(0,0,0,0.35);"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="4" cy="19" r="3"/><circle cx="19" cy="19" r="3"/><path d="M12 19V9l-3 3"/><path d="M9 12h6l3-6h-4l-2-3H7"/></svg></div>`,
            iconSize: [28, 28],
            iconAnchor: [14, 14],
          });
          L.marker([f.latitude, f.longitude], { icon: fmIcon })
            .bindTooltip(buildFieldmanTooltip(f.fieldman_id, "#ef4444", {}), { direction: "auto", offset: [0, -16], className: "preview-tt" })
            .addTo(layers.overviewFieldmen);
        }
      };
      _renderOverviewMarkers();

      // Re-render overview markers on map move (throttled, skip during zoom animation)
      let _moveTimer = null;
      let _isZooming = false;
      map.on("zoomstart", () => { _isZooming = true; });
      const _onMoveEnd = () => {
        _isZooming = false;
        clearTimeout(_moveTimer);
        _moveTimer = setTimeout(_renderOverviewMarkers, 300);
      };
      map.on("moveend", _onMoveEnd);
      map.on("zoomend", _onMoveEnd);

      // FINAL safety: forcefully remove layer groups that should be hidden
      if (pickerMode === "task") {
        map.removeLayer(layers.fieldmen);
        map.removeLayer(layers.overviewFieldmen);
      } else if (pickerMode === "fieldman") {
        map.removeLayer(layers.tasks);
        map.removeLayer(layers.overviewTasks);
      }

      if (animateRoutes) map.getContainer().classList.add("route-animate");
      else map.getContainer().classList.remove("route-animate");

      // Cleanup: remove moveend/zoomend listeners added for viewport culling
      return () => {
        if (mapRef.current) {
          mapRef.current.map.off("moveend", _onMoveEnd);
          mapRef.current.map.off("zoomend", _onMoveEnd);
        }
        clearTimeout(_moveTimer);
      };
    }, [mapReady, drawRoutes, drawOverview, animateRoutes, pickerMode]);

    // ── AGGRESSIVE layer isolation — runs separately on pickerMode ───
    useEffect(() => {
      if (!mapReady || !mapRef.current) return;
      const { map, layers } = mapRef.current;

      const enforce = () => {
        if (!mapRef.current) return;
        if (pickerMode === "task") {
          // Clear contents + remove from map
          layers.fieldmen.clearLayers();
          layers.overviewFieldmen.clearLayers();
          map.removeLayer(layers.fieldmen);
          map.removeLayer(layers.overviewFieldmen);
        } else if (pickerMode === "fieldman") {
          layers.tasks.clearLayers();
          layers.overviewTasks.clearLayers();
          map.removeLayer(layers.tasks);
          map.removeLayer(layers.overviewTasks);
        } else {
          if (!map.hasLayer(layers.tasks)) map.addLayer(layers.tasks);
          if (!map.hasLayer(layers.overviewTasks)) map.addLayer(layers.overviewTasks);
          if (!map.hasLayer(layers.fieldmen)) map.addLayer(layers.fieldmen);
          if (!map.hasLayer(layers.overviewFieldmen)) map.addLayer(layers.overviewFieldmen);
        }
      };

      // Run immediately AND once delayed to catch any race conditions
      enforce();
      const t1 = setTimeout(enforce, 200);

      return () => { clearTimeout(t1); };
    }, [pickerMode, mapReady]);

    // ── H3 grid (full viewport coverage + backend density data) ─────
    const [h3Loading, setH3Loading] = useState(false);
    const [mapBoundsRaw, setMapBoundsRaw] = useState(null);
    const mapBounds = useDebounce(mapBoundsRaw, 400); // debounce viewport changes for perf

    // Track map viewport changes
    useEffect(() => {
      if (!mapReady || !mapRef.current) return;
      const map = mapRef.current.map;
      const updateBounds = () => {
        const b = map.getBounds();
        setMapBoundsRaw({
          north: b.getNorth(), south: b.getSouth(),
          east: b.getEast(), west: b.getWest(),
        });
        setMapZoom(map.getZoom());
      };
      updateBounds();
      map.on("moveend", updateBounds);
      map.on("zoomend", updateBounds);
      return () => { map.off("moveend", updateBounds); map.off("zoomend", updateBounds); };
    }, [mapReady]);

    useEffect(() => {
      if (!mapReady || !mapRef.current || !h3) return;
      const { layers } = mapRef.current;
      layers.h3.clearLayers();
      if (!showH3 || !mapBounds) return;

      let cancelled = false;
      setH3Loading(true);

      // Limit resolution to avoid rendering too many cells
      const effectiveRes = Math.min(Number(h3Resolution), 10);

      // Build viewport polygon for h3.polygonToCells [lat, lng] pairs
      const { north, south, east, west } = mapBounds;
      const pad = 0.01;
      const polygon = [
        [north + pad, west - pad],
        [north + pad, east + pad],
        [south - pad, east + pad],
        [south - pad, west - pad],
      ];

      let viewportCells;
      try {
        viewportCells = h3.polygonToCells(polygon, effectiveRes);
      } catch (e) {
        console.warn("H3 polygonToCells error:", e);
        setH3Loading(false);
        return;
      }

      // Cap at 3000 cells to maintain performance
      if (viewportCells.length > 3000) {
        viewportCells = viewportCells.slice(0, 3000);
      }

      // ── Build density map from client-side data (always in sync) ──
      const dataMap = {};
      if (h3 && overview) {
        (overview.tasks || []).forEach((t) => {
          if (t.latitude != null && t.longitude != null) {
            try {
              const cell = h3.latLngToCell(t.latitude, t.longitude, effectiveRes);
              if (!dataMap[cell]) {
                const center = h3.cellToLatLng(cell);
                dataMap[cell] = { cell, task_count: 0, fieldman_count: 0, center_lat: center[0], center_lng: center[1] };
              }
              dataMap[cell].task_count += 1;
            } catch (_) {}
          }
        });
        (overview.fieldmen || []).forEach((f) => {
          if (f.latitude != null && f.longitude != null) {
            try {
              const cell = h3.latLngToCell(f.latitude, f.longitude, effectiveRes);
              if (!dataMap[cell]) {
                const center = h3.cellToLatLng(cell);
                dataMap[cell] = { cell, task_count: 0, fieldman_count: 0, center_lat: center[0], center_lng: center[1] };
              }
              dataMap[cell].fieldman_count += 1;
            } catch (_) {}
          }
        });
      }
      setH3Loading(false);
      const maxDensity = Math.max(1, ...Object.values(dataMap).map((p) => (p.task_count || 0) + (p.fieldman_count || 0)));

      // Render all viewport cells — split into empty grid (lightweight) and data cells (full)
      const emptyFeatures = [];
      const dataFeatures = [];
      viewportCells.forEach((cell) => {
        const boundary = h3.cellToBoundary(cell, true);
        const coords = [...boundary];
        coords.push(coords[0]);
        const data = dataMap[cell];
        if (data && (data.task_count > 0 || data.fieldman_count > 0)) {
          dataFeatures.push({
            type: "Feature",
            properties: { ...data, cell },
            geometry: { type: "Polygon", coordinates: [coords] },
          });
        } else {
          emptyFeatures.push({
            type: "Feature",
            properties: { cell },
            geometry: { type: "Polygon", coordinates: [coords] },
          });
        }
      });

      // Empty grid: very lightweight, no tooltips, no interactivity
      if (emptyFeatures.length) {
        L.geoJSON({ type: "FeatureCollection", features: emptyFeatures }, {
          interactive: false,
          style: () => ({
            color: "rgba(140, 140, 160, 0.2)",
            weight: 0.5,
            fillColor: "rgba(140, 140, 160, 0.02)",
            fillOpacity: 0.02,
          }),
        }).addTo(layers.h3);
      }

      // Data cells: full styling + tooltips
      if (dataFeatures.length) {
        L.geoJSON({ type: "FeatureCollection", features: dataFeatures }, {
          style: (feature) => {
            const { task_count = 0, fieldman_count = 0 } = feature.properties;
            const density = task_count + fieldman_count;
            const intensity = Math.min(density / maxDensity, 1);
            const r = Math.round(79 + intensity * 80);
            const g = Math.round(70 - intensity * 40);
            const b = Math.round(229 - intensity * 30);
            const fillOpacity = 0.2 + intensity * 0.5;
            return {
              color: `rgba(${r}, ${g}, ${b}, 0.55)`,
              weight: 1,
              fillColor: `rgba(${r}, ${g}, ${b}, ${fillOpacity})`,
              fillOpacity,
            };
          },
          onEachFeature: (feature, layer) => {
            const { cell, task_count = 0, fieldman_count = 0 } = feature.properties;
            layer.bindTooltip(
              `<div style="font-size:11px;line-height:1.4"><b>H3:</b> ${cell.substring(0,12)}…<br><b>Tasks:</b> ${task_count}<br><b>Fieldmen:</b> ${fieldman_count}</div>`,
              { sticky: true, direction: "top", className: "h3-tooltip" }
            );
          },
        }).addTo(layers.h3);
      }

      return () => { cancelled = true; };
    }, [mapReady, showH3, h3Resolution, mapBounds, overview]);

    // Invalidate map size when panel collapses/expands
    useEffect(() => {
      if (mapRef.current) setTimeout(() => mapRef.current.map.invalidateSize(), 300);
    }, [panelCollapsed]);

    const handleFit = () => {
      if (!mapRef.current || !mapPoints.length) return;
      const bounds = L.latLngBounds(mapPoints.map((p) => [p.lat, p.lng]));
      if (bounds.isValid()) mapRef.current.map.fitBounds(bounds.pad(0.2));
    };

    const handleClear = () => {
      setRoutes([]);
      setOverview({ tasks: [], fieldmen: [] });
      setRouteSummaries([]);
      // Clear persisted state so reload also starts clean
      try { ["vrp:routes", "vrp:routeSummaries", "vrp:overview", "vrp:jobId"].forEach(k => localStorage.removeItem(k)); } catch(_){}
    };

    const ok = !!(mapReady && L);

    // ── Render ───────────────────────────────────────────────────────
    return el("div", { className: "map-view-split", style: panelCollapsed ? { gridTemplateColumns: "0px 1fr" } : undefined },

      // ── LEFT PANEL: Generator + Settings (scrollable) ──────────────
      el("div", { className: "map-view-panel", style: panelCollapsed ? { display: "none" } : undefined },

        // Generator card
        el("div", { className: "card" },
          el("div", { className: "card-header" },
            el("span", { className: "card-title" }, el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("generator") } }), "Random Generator")
          ),
          el("div", { className: "form-grid" },
            el("div", { className: "field" },
              el("label", null, "Number of Jobs"),
              el("input", { type: "number", min: 1, max: 100, value: numJobs,
                onChange: (e) => setNumJobs(e.target.value) }),
              el("span", { className: "field-hint" }, "Generate N batches at once")
            ),
            el("div", { className: "field" },
              el("label", null, "Number of Fieldmen"),
              el("input", { type: "number", min: 0, max: 500, value: numFieldmen,
                onChange: (e) => setNumFieldmen(e.target.value) })
            ),
            el("div", { className: "field" },
              el("label", null, el("span", { className: "type-dot", style: { background: TASK_TYPE_COLORS.credit_investigation } }), " CI (Credit Investigation)"),
              el("input", { type: "number", min: 0, max: 1000, value: tasksCi,
                onChange: (e) => setTasksCi(e.target.value) })
            ),
            el("div", { className: "field" },
              el("label", null, el("span", { className: "type-dot", style: { background: TASK_TYPE_COLORS.demand_letter } }), " DL (Demand Letter)"),
              el("input", { type: "number", min: 0, max: 1000, value: tasksDl,
                onChange: (e) => setTasksDl(e.target.value) })
            ),
            el("div", { className: "field" },
              el("label", null, el("span", { className: "type-dot", style: { background: TASK_TYPE_COLORS.skips_collect } }), " S&C (Skips & Collect)"),
              el("input", { type: "number", min: 0, max: 1000, value: tasksSc,
                onChange: (e) => setTasksSc(e.target.value) })
            ),
            el("div", { className: "field" }),
            el("div", { className: "field span-2" },
              el("label", null, "Task Area"),
              el("select", { value: taskArea, onChange: (e) => { setTaskArea(e.target.value); if (e.target.value === "CUSTOM" && !customCenter) handleMapClickForCenter(); } },
                allAreas.map((a) => el("option", { key: a, value: a }, a === "CUSTOM" ? "Custom (Click Map)" : a))
              ),
              taskArea === "CUSTOM" && customCenter
                ? el("div", { style: { marginTop: 6, display: "flex", alignItems: "center", gap: 8 } },
                    el("span", { className: "field-hint", style: { margin: 0 } },
                      `Center: ${customCenter.lat.toFixed(5)}, ${customCenter.lng.toFixed(5)}`),
                    el("button", { className: "btn sm", onClick: handleMapClickForCenter, style: { fontSize: 11, padding: "2px 8px" } }, "Re-pick")
                  )
                : taskArea === "CUSTOM"
                  ? el("span", { className: "field-hint", style: { color: "var(--warning)" } }, "Click on the map to set center point")
                  : el("span", { className: "field-hint" }, "Area assigned to generated tasks")
            ),
            el("div", { className: "field span-2" },
              el("label", null, "Fieldman Areas"),
              el("div", { className: "area-checkboxes" },
                allAreas.map((a) =>
                  el("label", { key: a, className: "checkbox-label" },
                    el("input", { type: "checkbox", checked: fieldmanAreas.includes(a),
                      onChange: () => toggleFieldmanArea(a) }),
                    el("span", null, a)
                  )
                )
              ),
              el("span", { className: "field-hint" }, "Hold Ctrl/Cmd to select multiple areas")
            ),
            el("div", { className: "field span-2" },
              el("label", null, "Task Banks"),
              el("input", { type: "text", placeholder: "BPI:60,BDO:40", value: taskBanks,
                onChange: (e) => setTaskBanks(e.target.value) }),
              el("span", { className: "field-hint" }, "Format: BANK:%, e.g. BPI:60,BDO:40")
            ),
            el("div", { className: "field" },
              el("label", null, "Area Radius (km)"),
              el("input", { type: "number", min: 1, max: 50, value: areaRadius,
                onChange: (e) => setAreaRadius(e.target.value) })
            ),
            el("div", { className: "field" },
              el("label", null, "Service Time (min)"),
              el("input", { type: "number", min: 1, max: 480, value: serviceTime,
                onChange: (e) => setServiceTime(e.target.value) })
            ),
            el("div", { className: "field span-2" },
              el("label", null, `Scatterness: ${scatterness}%`),
              el("div", { className: "scatter-slider" },
                el("span", { className: "scatter-label" }, "Clustered"),
                el("input", { type: "range", min: 0, max: 100, value: scatterness,
                  onChange: (e) => setScatterness(e.target.value) }),
                el("span", { className: "scatter-label" }, "Scattered")
              )
            )
          ),
          el("div", { className: "actions" },
            el("button", { className: "btn primary lg", onClick: handleRandomize, disabled: genLoading || (taskArea === "CUSTOM" && !customCenter) },
              genLoading ? "Generating..." : [
                el("span", { key:"i", dangerouslySetInnerHTML: { __html: icon("generator") } }),
                numJobs > 1 ? ` Generate ${numJobs} Jobs` : " Randomize"
              ]),
            el("button", { className: "btn danger lg", onClick: handleReset, disabled: genLoading },
              el("span", { dangerouslySetInnerHTML: { __html: icon("trash") } }), " Reset All")
          ),
          genResult && el("div", { className: "output-box", style: { marginTop: "12px" } }, genResult)
        ),

        // Route Settings card
        el("div", { className: "card" },
          el("div", { className: "card-header" },
            el("span", { className: "card-title" }, el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("sliders") } }), "Route Settings")
          ),
          el("div", { className: "form-grid" },
            el("div", { className: "field span-2" },
              el("label", null, "Clustering Mode"),
              el("select", { value: clusterMode, onChange: (e) => setClusterMode(e.target.value) },
                el("option", { value: "h3" }, "H3 Clustering"),
                el("option", { value: "km" }, "KM: Max radius"),
                el("option", { value: "manual_area" }, "Area Only")
              ),
              el("span", { className: "field-hint" }, "KM: Use max radius. Area: Match fieldman to task area")
            ),
            el("div", { className: "field span-2" },
              el("label", null, "Areas (comma-separated)"),
              el("input", { type: "text", value: optAreas,
                onChange: (e) => setOptAreas(e.target.value) }),
              el("span", { className: "field-hint" }, "Areas will be assigned to fieldmen and tasks")
            ),
            el("div", { className: "field span-2" },
              el("label", { className: "checkbox-label", style: { textTransform: "none", letterSpacing: 0 } },
                el("input", { type: "checkbox", checked: bankQuotaEnabled,
                  onChange: (e) => setBankQuotaEnabled(e.target.checked) }),
                el("span", { style: { color: "#16a34a", fontWeight: 600 } }, "Enable Bank Quota Filtering")
              )
            ),
            bankQuotaEnabled && el("div", { className: "field span-2" },
              el("label", null, "Target Distribution"),
              el("input", { type: "text", value: bankQuotaDist,
                onChange: (e) => setBankQuotaDist(e.target.value) }),
              el("span", { className: "field-hint" }, "VRP will enforce this global bank distribution")
            ),
            el("div", { className: "field span-2" },
              el("label", null, "Service Time per Task (minutes)"),
              el("input", { type: "number", min: 1, max: 480, value: optServiceTime,
                onChange: (e) => setOptServiceTime(e.target.value),
                style: { borderColor: "#4f46e5", borderWidth: "2px" } })
            ),
            el("div", { className: "field" },
              el("label", null, "Max Radius (km)"),
              el("input", { type: "number", min: 1, max: 50, value: maxRadius,
                onChange: (e) => setMaxRadius(e.target.value) })
            ),
            el("div", { className: "field" },
              el("label", { className: "checkbox-label", style: { textTransform: "none", letterSpacing: 0, marginTop: "22px" } },
                el("input", { type: "checkbox", checked: nearbyFilter,
                  onChange: (e) => setNearbyFilter(e.target.checked) }),
                el("span", null, "Nearby Filter")
              )
            ),
            nearbyFilter && el("div", { className: "field span-2" },
              el("span", { className: "field-hint" }, "Only assign tasks within this distance from fieldmen")
            ),
            el("div", { className: "field" },
              el("label", null, "Priority Min"),
              el("input", { type: "number", step: "0.1", min: 0, placeholder: "e.g. 1.0",
                value: optPriorityMin, onChange: (e) => setOptPriorityMin(e.target.value) })
            ),
            el("div", { className: "field" },
              el("label", null, "Priority Max"),
              el("input", { type: "number", step: "0.1", min: 0, placeholder: "e.g. 100.0",
                value: optPriorityMax, onChange: (e) => setOptPriorityMax(e.target.value) })
            ),
            el("div", { className: "field span-2" },
              el("span", { className: "field-hint" }, "Rescale all task priorities (float, e.g. 1.0–100.0) into this range before optimization (higher = served first)")
            ),
            el("div", { className: "field span-2" },
              el("label", { className: "checkbox-label", style: { textTransform: "none", letterSpacing: 0 } },
                el("input", { type: "checkbox", checked: showRadiusCircles,
                  onChange: (e) => setShowRadiusCircles(e.target.checked) }),
                el("span", null, "Show radius circles on map")
              )
            )
          ),
          el("div", { className: "actions", style: { gap: "8px" } },
            el("button", { className: "btn primary lg", onClick: handleOptimize, disabled: optLoading, style: { flex: 1 } },
              optLoading ? "Optimizing..." : [el("span", { key:"i", dangerouslySetInnerHTML: { __html: icon("rocket") } }), " Optimize Routes"]),
            routes.length > 0 && el("button", {
              className: "btn ghost lg",
              style: { color: "var(--danger)", borderColor: "var(--danger)" },
              onClick: () => {
                setRoutes([]);
                setRouteSummaries([]);
                setJobId("");
                appendLog("Routes cleared");
              },
            }, el("span", { dangerouslySetInnerHTML: { __html: icon("x") } }), " Clear Routes")
          ),
          el("div", { style: { borderTop: "1px solid var(--border)", marginTop: "16px", paddingTop: "16px" } },
            el("div", { className: "card-header", style: { marginBottom: "8px", paddingBottom: "8px" } },
              el("span", { className: "card-title" }, "Data")
            ),
            el("div", { className: "actions" },
              el("button", { className: "btn ghost", onClick: handleExport }, el("span", { dangerouslySetInnerHTML: { __html: icon("upload") } }), " Export JSON"),
              el("button", { className: "btn ghost", onClick: handleImport }, el("span", { dangerouslySetInnerHTML: { __html: icon("download") } }), " Import JSON")
            )
          ),
          optResult && el("div", { className: "output-box", style: { marginTop: "12px" } }, optResult)
        ),

        // Route list card (if routes exist)
        routeSummaries.length > 0 && el("div", { className: "card" },
          el("div", { className: "card-header" },
            el("span", { className: "card-title" }, el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("truck") } }), "Routes"),
            el("span", { className: "pill accent" }, `${routeSummaries.length}`)
          ),
          el("div", { className: "route-list" },
            routeSummaries.map((r, i) =>
              el("div", { key: i, className: "route-item" },
                el("div", { className: "route-item-left" },
                  el("div", { className: "route-swatch", style: { background: PALETTE[i % PALETTE.length] } }),
                  el("span", null, r.fieldman_id ? r.fieldman_id.substring(0, 8) + "..." : `Route ${i + 1}`)
                ),
                el("div", { className: "route-item-right" }, `${r.tasks} tasks`)
              )
            )
          )
        ),

        // ── Map Picker card ────────────────────────────────────────────
        el("div", { className: "card" },
          el("div", { className: "card-header" },
            el("span", { className: "card-title" }, el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("target") } }), "Map Picker"),
            pickerMode && el("span", { className: `pill ${pickerMode === "task" ? "accent" : "danger"}` },
              pickerMode === "task" ? "Task Mode" : "Fieldman Mode")
          ),
          el("div", { className: "picker-toolbar" },
            el("div", { className: "picker-mode-buttons" },
              el("button", {
                className: `btn ${pickerMode === "task" ? "primary" : "ghost"} sm`,
                onClick: () => startPicker("task"),
                title: "Place tasks on the map"
              }, el("span", { dangerouslySetInnerHTML: { __html: icon("clipboard") } }), " Place Tasks"),
              el("button", {
                className: `btn ${pickerMode === "fieldman" ? "danger" : "ghost"} sm`,
                onClick: () => startPicker("fieldman"),
                title: "Place fieldmen on the map"
              }, el("span", { dangerouslySetInnerHTML: { __html: icon("motorcycle") } }), " Place Fieldmen"),
              el("button", {
                className: `btn ${pickerMode === "move_fm" ? "warning" : "ghost"} sm`,
                onClick: () => startPicker("move_fm"),
                title: "Move a fieldman to a new location"
              }, el("span", { dangerouslySetInnerHTML: { __html: icon("map") } }), " Move FM"),
              pickerMode && el("button", {
                className: "btn ghost sm",
                onClick: endPicker,
                title: "Stop placing"
              }, el("span", { dangerouslySetInnerHTML: { __html: icon("x") } }), " Done")
            ),
            // Picker options (shown when picker is active)
            pickerMode === "task" && el("div", { className: "picker-options" },
              el("div", { className: "field" },
                el("label", null, "Task Type"),
                el("select", { value: pickerTaskType, onChange: (e) => setPickerTaskType(e.target.value), style: { fontSize: 11 } },
                  el("option", { value: "credit_investigation" }, "Credit Investigation"),
                  el("option", { value: "skips_collect" }, "Skips & Collect"),
                  el("option", { value: "demand_letter" }, "Demand Letter")
                )
              ),
              el("div", { className: "field" },
                el("label", null, "Bank"),
                el("input", { type: "text", placeholder: "BPI, BDO, etc.", value: pickerBank,
                  onChange: (e) => setPickerBank(e.target.value), style: { fontSize: 11 } })
              )
            ),
            pickerMode === "fieldman" && el("div", { className: "picker-options" },
              el("div", { className: "field" },
                el("label", null, "Fieldman Area"),
                el("select", { value: pickerFieldmanArea, onChange: (e) => setPickerFieldmanArea(e.target.value), style: { fontSize: 11 } },
                  allAreas.filter(a => a !== "CUSTOM").map(a => el("option", { key: a, value: a }, a))
                )
              )
            ),
            pickerMode === "move_fm" && el("div", { className: "picker-options" },
              el("div", { className: "field" },
                el("label", null, "Select Fieldman to Move"),
                overview.fieldmen.length > 0
                  ? el("select", { value: moveFmId, onChange: (e) => setMoveFmId(e.target.value), style: { fontSize: 11 } },
                      el("option", { value: "" }, "-- Select FM --"),
                      overview.fieldmen.map(f => el("option", { key: f.fieldman_id, value: f.fieldman_id },
                        `${f.fieldman_id.substring(0, 12)}... (${f.area || "N/A"})`
                      ))
                    )
                  : el("input", { type: "text", placeholder: "Enter Fieldman UUID...", value: moveFmId,
                      onChange: (e) => setMoveFmId(e.target.value), style: { fontSize: 11 } })
              ),
              moveFmId && el("span", { className: "field-hint", style: { color: "var(--success)" } },
                "Click on the map to set new location")
            )
          ),
          // Picked items list
          pickedItems.length > 0 && el("div", { className: "picked-items-list" },
            el("div", { className: "panel-section-title" }, `Picked Items (${pickedItems.length})`),
            pickedItems.map((item, i) =>
              el("div", { key: item.id, className: "picked-item" },
                el("div", { className: "picked-item-left" },
                  el("div", { className: "picked-item-dot", style: {
                    background: item.type === "task" ? (TASK_TYPE_COLORS[item.task_type] || "#6366f1") : "#ef4444"
                  } }),
                  el("div", { className: "picked-item-info" },
                    el("span", { className: "picked-item-label" },
                      item.type === "task" ? (TASK_TYPE_LABELS[item.task_type] || "Task") : "Fieldman",
                      item.type === "task" && item.manual_priority != null && el("span", { className: "priority-badge-inline", title: `Manual priority: ${item.manual_priority}` },
                        `P${item.manual_priority}`
                      )
                    ),
                    el("span", { className: "picked-item-address" },
                      item.address || `${item.latitude.toFixed(4)}, ${item.longitude.toFixed(4)}`)
                  )
                ),
                el("div", { className: "picked-item-actions" },
                  item.type === "task" && el("button", { className: "btn ghost sm picked-item-priority", title: "Edit priority",
                    onClick: () => {
                      setEditPriorityTask({ task_id: item.id, current: item.manual_priority ?? null });
                      setEditPriorityInput(item.manual_priority != null ? String(item.manual_priority) : "");
                    }
                  },
                    el("span", { dangerouslySetInnerHTML: { __html: icon("sliders") } })
                  ),
                  el("button", { className: "btn ghost sm picked-item-delete", onClick: () => removePickedItem(item, i),
                    title: "Remove this item" },
                    el("span", { dangerouslySetInnerHTML: { __html: icon("trash") } })
                  )
                )
              )
            ),
            el("div", { style: { padding: "8px 0 0", borderTop: "1px solid var(--border)", marginTop: 8 } },
              el("button", { className: "btn ghost sm", style: { fontSize: 10, color: "var(--danger)" },
                onClick: async () => {
                  for (let i = pickedItems.length - 1; i >= 0; i--) {
                    try {
                      const item = pickedItems[i];
                      const endpoint = item.type === "task" ? "task" : "fieldman";
                      await fetchJson(`${API_VRP}/picker/${endpoint}/${item.id}`, { method: "DELETE" });
                    } catch (_) {}
                  }
                  // Clear all picker markers from map
                  pickerMarkersRef.current.forEach(m => { if (mapRef.current) mapRef.current.map.removeLayer(m); });
                  pickerMarkersRef.current = [];
                  setPickedItems([]);
                  appendLog("Cleared all picked items");
                  invalidateCache();
                  try { const ov = await fetchJson(`${API_VRP}/overview`); setOverview(ov); } catch (_) {}
                }
              }, "Clear All Picked")
            )
          ),
          !pickerMode && pickedItems.length === 0 && el("div", { style: { padding: "12px 0", textAlign: "center", color: "var(--text-muted)", fontSize: 11 } },
            "Click ", el("strong", null, "Place Tasks"), " or ", el("strong", null, "Place Fieldmen"), " then click on the map to add points."
          )
        ),

        // ── FM Location Update card (ported from source repo) ──────────
        el("div", { className: "card" },
          el("div", { className: "card-header" },
            el("span", { className: "card-title" }, el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("map") } }), "Update FM Location")
          ),
          el("div", { className: "fm-location-info" },
            el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("realtime") } }),
            el("span", null, " Update a fieldman's current location. This overrides their home location for route planning. Cached for 10 minutes.")
          ),
          el("div", { className: "form-grid" },
            el("div", { className: "field span-2" },
              el("label", null, "Fieldman ID"),
              overview.fieldmen.length > 0
                ? el("select", { id: "fmLocId",
                    onChange: (e) => {
                      const fmIdInput = e.target.value;
                      if (fmIdInput) {
                        const fm = overview.fieldmen.find(f => f.fieldman_id === fmIdInput);
                        if (fm) {
                          setFmLocLat(String(fm.latitude));
                          setFmLocLng(String(fm.longitude));
                        }
                      }
                    }
                  },
                    el("option", { value: "" }, "-- Select Fieldman --"),
                    overview.fieldmen.map(f => el("option", { key: f.fieldman_id, value: f.fieldman_id },
                      `${f.fieldman_id.substring(0, 16)}... (${f.area || ""})`
                    ))
                  )
                : el("input", { id: "fmLocId", type: "text", placeholder: "Enter Fieldman UUID..." })
            ),
            el("div", { className: "field" },
              el("label", null, "Latitude"),
              el("input", { id: "fmLocLat", type: "number", step: "0.0001", placeholder: "14.5995",
                value: fmLocLat, onChange: (e) => setFmLocLat(e.target.value) })
            ),
            el("div", { className: "field" },
              el("label", null, "Longitude"),
              el("input", { id: "fmLocLng", type: "number", step: "0.0001", placeholder: "120.9842",
                value: fmLocLng, onChange: (e) => setFmLocLng(e.target.value) })
            )
          ),
          el("div", { className: "actions", style: { gap: 8, display: "flex", flexWrap: "wrap" } },
            el("button", {
              className: `btn ${fmLocPickerActive ? "warning" : "ghost"} sm`,
              onClick: () => {
                if (pickerMode) { appendLog("End picker mode first"); return; }
                setFmLocPickerActive(!fmLocPickerActive);
                if (!fmLocPickerActive) appendLog("Click on the map to pick a location...");
              }
            },
              el("span", { dangerouslySetInnerHTML: { __html: icon("target") } }),
              fmLocPickerActive ? " Cancel Pick" : " Pick from Map"
            ),
            el("button", { className: "btn primary sm", onClick: async () => {
              const fmId = document.getElementById("fmLocId").value;
              const lat = parseFloat(fmLocLat);
              const lng = parseFloat(fmLocLng);
              if (!fmId || isNaN(lat) || isNaN(lng)) {
                appendLog("FM Location: Please fill all fields");
                return;
              }
              try {
                const data = await fetchJson(`${API_VRP}/fieldman/${fmId}/location`, {
                  method: "PUT",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ latitude: lat, longitude: lng }),
                });
                appendLog(`FM ${fmId.substring(0, 8)} location updated to ${lat.toFixed(5)}, ${lng.toFixed(5)} (cached 10 min)`);
                // Update route FM positions in-place to avoid stale markers
                setRoutes(prev => prev.map(r =>
                  r.fieldman_id === fmId ? { ...r, start_lat: lat, start_long: lng } : r
                ));
                invalidateCache("overview");
                try { const ov = await fetchJson(`${API_VRP}/overview`); setOverview(ov); } catch (_) {}
              } catch (err) {
                appendLog(`FM Location update failed: ${err.message}`);
              }
            } },
              el("span", { dangerouslySetInnerHTML: { __html: icon("check") } }), " Update Location"
            )
          ),
          fmLocPickerActive && el("div", { className: "field-hint", style: { marginTop: 8, color: "var(--accent)", fontWeight: 500 } },
            el("span", { dangerouslySetInnerHTML: { __html: icon("target") } }),
            " Click on the map to pick coordinates. Press Esc to cancel."
          )
        )
      ),

      // ── RIGHT PANEL: Map ───────────────────────────────────────────
      el("div", { className: "map-view-main" },
        el("div", { className: "map-controls-bar" },
          el("button", { className: "btn ghost sm", onClick: () => setPanelCollapsed(p => !p), title: panelCollapsed ? "Show panel" : "Hide panel" },
            el("span", { dangerouslySetInnerHTML: { __html: panelCollapsed ? icon("eye") : icon("x") } })),
          el("span", { className: "divider" }),
          el("button", { className: "btn ghost sm", onClick: handleFit }, el("span", { dangerouslySetInnerHTML: { __html: icon("ruler") } }), " Fit"),
          el("button", { className: "btn ghost sm", onClick: handleClear }, el("span", { dangerouslySetInnerHTML: { __html: icon("trash") } }), " Clear"),
          el("button", { className: "btn ghost sm", onClick: () => setShowH3((p) => !p) },
            el("span", { dangerouslySetInnerHTML: { __html: showH3 ? icon("grid") : icon("gridHide") } }), showH3 ? " H3" : " H3"),
          showH3 && el("div", { style: { display: "flex", alignItems: "center", gap: 4 } },
            el("button", {
              className: `btn ghost sm${h3AutoResolution ? " active" : ""}`,
              onClick: () => setH3AutoResolution(p => !p),
              title: h3AutoResolution ? "Auto resolution ON — click for manual" : "Manual resolution — click for auto",
              style: { fontSize: 10, padding: "2px 6px", minWidth: 36, border: h3AutoResolution ? "1px solid var(--accent)" : "1px solid var(--border)" },
            }, h3AutoResolution ? "Auto" : "Man."),
            el("input", {
              type: "range", min: 1, max: 12, step: 1, value: h3Resolution,
              style: { width: 70, height: 16, accentColor: h3AutoResolution ? "var(--text-muted)" : "var(--accent)", opacity: h3AutoResolution ? 0.5 : 1 },
              onChange: (e) => {
                setH3AutoResolution(false);
                state.setH3Resolution(Number(e.target.value));
              },
              title: h3AutoResolution ? `H3 Resolution: ${h3Resolution} (auto)` : `H3 Resolution: ${h3Resolution} (manual)`,
            }),
            el("span", { style: { fontSize: 10, color: "var(--text-muted)", fontFamily: "JetBrains Mono, monospace", minWidth: 18 } }, h3Resolution),
            h3ResolutionInfo && h3AutoResolution && el("span", {
              style: { fontSize: 9, color: "var(--accent)", fontStyle: "italic" },
              title: `Auto-suggested: res ${h3ResolutionInfo.auto_suggested}, bbox: ${(h3ResolutionInfo.bbox_diagonal_km || 0).toFixed(1)}km`,
            }, "auto"),
            h3Loading && el("span", { style: { fontSize: 10, color: "var(--accent)" } }, "⏳"),
          ),
          el("button", { className: "btn ghost sm", onClick: () => setAnimateRoutes((p) => !p) },
            el("span", { dangerouslySetInnerHTML: { __html: animateRoutes ? icon("pause") : icon("play") } }), animateRoutes ? " Stop" : " Animate"),
          el("span", { className: "divider" }),
          // Data type selector
          el("div", { className: "data-filter-group" },
            el("button", { className: `btn ghost sm${mapDataFilter === "all" ? " active" : ""}`, onClick: () => setMapDataFilter("all") }, "All"),
            el("button", { className: `btn ghost sm${mapDataFilter === "tasks" ? " active" : ""}`, onClick: () => setMapDataFilter("tasks") }, "Tasks"),
            el("button", { className: `btn ghost sm${mapDataFilter === "fieldmen" ? " active" : ""}`, onClick: () => setMapDataFilter("fieldmen") }, "FMs")
          ),
          el("span", { style: { flex: 1 } }),
          el("span", { style: { fontSize: 11, color: "var(--text-muted)", fontFamily: "JetBrains Mono, monospace" } },
            `${overview.tasks.length} tasks · ${overview.fieldmen.length} fieldmen · ${routes.length} routes`)
        ),
        el("div", { className: "map-container", style: { position: "relative" } },
          el("div", { className: "map-view", ref: mapContainerRef }),

          // ── Picker mode: floating center pin ─────────────────────────
          (pickerMode || fmLocPickerActive) && el("div", { className: "picker-center-pin", key: "picker-pin" },
            el("div", { dangerouslySetInnerHTML: { __html: `<svg xmlns="http://www.w3.org/2000/svg" width="40" height="56" viewBox="0 0 32 44"><defs><filter id="pds" x="-20%" y="-10%" width="140%" height="130%"><feDropShadow dx="0" dy="2" stdDeviation="2" flood-opacity="0.3"/></filter></defs><path d="M16 0C7.16 0 0 7.16 0 16c0 12 16 28 16 28s16-16 16-28C32 7.16 24.84 0 16 0z" fill="${fmLocPickerActive ? "#6366f1" : pickerMode === "task" ? (TASK_TYPE_COLORS[pickerTaskType] || "#6366f1") : "#ef4444"}" filter="url(#pds)"/><circle cx="16" cy="14" r="7" fill="#fff"/><circle cx="16" cy="14" r="3" fill="${fmLocPickerActive ? "#6366f1" : pickerMode === "task" ? (TASK_TYPE_COLORS[pickerTaskType] || "#6366f1") : "#ef4444"}"/></svg>` } }),
            el("div", { className: "picker-pin-shadow" })
          ),

          // ── FM Location picker banner ────────────────────────────────
          fmLocPickerActive && el("div", { className: "picker-banner picker-banner-fieldman", key: "fmloc-banner" },
            el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("target") } }),
            el("span", null, " Click on the map to set fieldman location"),
            el("button", { className: "btn sm", onClick: () => setFmLocPickerActive(false),
              style: { marginLeft: "auto", fontSize: 11, padding: "4px 14px", background: "rgba(255,255,255,0.2)", border: "1px solid rgba(255,255,255,0.4)", color: "#fff", fontWeight: 600 } },
              "Cancel"),
            el("span", { style: { fontSize: 10, color: "rgba(255,255,255,0.5)", marginLeft: 8 } }, "Esc")
          ),

          // ── Picker mode: banner ──────────────────────────────────────
          pickerMode && el("div", { className: `picker-banner ${pickerMode === "task" ? "picker-banner-task" : "picker-banner-fieldman"}`, key: "picker-banner" },
            el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("target") } }),
            el("span", null, pickerMode === "task"
              ? ` Placing Tasks (${TASK_TYPE_LABELS[pickerTaskType] || pickerTaskType})`
              : " Placing Fieldmen"),
            pickerSaving && el("span", { style: { marginLeft: 8, fontSize: 11, opacity: 0.8 } }, "Saving..."),
            el("span", { style: { marginLeft: "auto", fontSize: 11, color: "rgba(255,255,255,0.7)" } },
              `${pickedItems.filter(p => p.type === pickerMode).length} placed`),
            el("button", { className: "btn sm", onClick: endPicker,
              style: { marginLeft: 12, fontSize: 11, padding: "4px 14px", background: "rgba(255,255,255,0.2)", border: "1px solid rgba(255,255,255,0.4)", color: "#fff", fontWeight: 600 } },
              "Done Picking"),
            el("span", { style: { fontSize: 10, color: "rgba(255,255,255,0.5)", marginLeft: 8 } }, "Esc")
          ),
          ok && el("div", { className: "map-status-bar" },
            `Tiles: ${tileStatus.loaded}✓ ${tileStatus.failed}✗${tileStatus.fallback ? " (fallback)" : ""}${tileStatus.size ? ` | ${tileStatus.size.x}×${tileStatus.size.y}` : ""}`
          ),
          ok && el("div", { className: "map-legend" },
            el("div", { className: "legend-item" },
              el("span", { className: "legend-swatch", style: { background: TASK_TYPE_COLORS.credit_investigation } }),
              el("span", null, "CI")
            ),
            el("div", { className: "legend-item" },
              el("span", { className: "legend-swatch", style: { background: TASK_TYPE_COLORS.skips_collect } }),
              el("span", null, "S&C")
            ),
            el("div", { className: "legend-item" },
              el("span", { className: "legend-swatch", style: { background: TASK_TYPE_COLORS.demand_letter } }),
              el("span", null, "DL")
            ),
            el("div", { className: "legend-item" },
              el("span", { className: "legend-swatch", style: { background: FIELDMAN_COLOR } }),
              el("span", null, "FM")
            ),
            routes.length > 0 && PALETTE.slice(0, Math.min(routes.length, 8)).map((c, i) =>
              el("div", { key: `r${i}`, className: "legend-item" },
                el("span", { className: "legend-swatch", style: { background: c } }),
                el("span", null, `R${i + 1}`)
              )
            )
          ),

          // ── Priority Popup (on task placement) ───────────────────────
          priorityPopup && el("div", { className: "priority-popup-overlay", onClick: () => { setPriorityPopup(null); appendLog("Task placement cancelled"); } },
            el("div", { className: "priority-popup", onClick: (e) => e.stopPropagation() },
              el("div", { className: "priority-popup-header" },
                el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("sliders") } }),
                el("span", null, " Set Task Priority")
              ),
              el("div", { className: "priority-popup-body" },
                el("p", { className: "priority-popup-hint" },
                  "Set a manual priority (0.0–100.0) for this task. Higher = served first. Accepts decimals (e.g. 75.5). Leave blank to use the route settings default."
                ),
                el("div", { className: "priority-popup-input-row" },
                  el("label", null, "Priority"),
                  el("input", {
                    type: "number", min: 0, max: 100, step: 0.1,
                    placeholder: "e.g. 75.5",
                    value: priorityInput,
                    onChange: (e) => setPriorityInput(e.target.value),
                    autoFocus: true,
                    onKeyDown: (e) => {
                      if (e.key === "Enter") confirmPlaceTask(priorityInput.trim() === "" ? null : Number(priorityInput));
                      if (e.key === "Escape") { setPriorityPopup(null); appendLog("Task placement cancelled"); }
                    }
                  })
                )
              ),
              el("div", { className: "priority-popup-actions" },
                el("button", { className: "btn ghost sm", onClick: () => { setPriorityPopup(null); appendLog("Task placement cancelled"); } }, "Cancel"),
                el("button", { className: "btn secondary sm", onClick: () => confirmPlaceTask(null) },
                  el("span", { dangerouslySetInnerHTML: { __html: icon("rocket") } }), " Skip (Default)"
                ),
                el("button", { className: "btn primary sm",
                  disabled: priorityInput.trim() !== "" && (isNaN(Number(priorityInput)) || Number(priorityInput) < 0 || Number(priorityInput) > 100),
                  onClick: () => confirmPlaceTask(priorityInput.trim() === "" ? null : Number(priorityInput))
                },
                  el("span", { dangerouslySetInnerHTML: { __html: icon("target") } }), " Set Priority"
                )
              )
            )
          ),

          // ── Edit Priority Modal (for existing tasks) ─────────────────
          editPriorityTask && el("div", { className: "priority-popup-overlay", onClick: () => setEditPriorityTask(null) },
            el("div", { className: "priority-popup", onClick: (e) => e.stopPropagation() },
              el("div", { className: "priority-popup-header" },
                el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("sliders") } }),
                el("span", null, " Edit Task Priority")
              ),
              el("div", { className: "priority-popup-body" },
                el("p", { className: "priority-popup-hint" },
                  `Current: ${editPriorityTask.current != null ? editPriorityTask.current : "default"}. Enter a float value (0.0–100.0, e.g. 85.5) or clear to reset to default.`
                ),
                el("div", { className: "priority-popup-input-row" },
                  el("label", null, "Priority"),
                  el("input", {
                    type: "number", min: 0, max: 100, step: 0.1,
                    placeholder: "e.g. 85.0 (blank = default)",
                    value: editPriorityInput,
                    onChange: (e) => setEditPriorityInput(e.target.value),
                    autoFocus: true,
                    onKeyDown: (e) => {
                      if (e.key === "Enter") handleSavePriority();
                      if (e.key === "Escape") setEditPriorityTask(null);
                    }
                  })
                )
              ),
              el("div", { className: "priority-popup-actions" },
                el("button", { className: "btn ghost sm", onClick: () => setEditPriorityTask(null) }, "Cancel"),
                el("button", { className: "btn warning sm", onClick: () => { setEditPriorityInput(""); } },
                  "Reset to Default"
                ),
                el("button", { className: "btn primary sm", onClick: handleSavePriority },
                  "Save Priority"
                )
              )
            )
          )
        )
      )
    );
  });

  /* ═══════════════════════════════════════════════════════════════════
     PAGE: Realtime
     ═══════════════════════════════════════════════════════════════════ */
  const RealtimePage = React.memo(function RealtimePage({ state }) {
    const { jobId, setJobId, logs, appendLog, wsRef, setRoutes, setRouteSummaries, setJobResult } = state;

    const handleConnect = () => {
      if (!jobId) return;
      if (wsRef.current) wsRef.current.close();
      const ws = new WebSocket(`${WS_BASE}/api/v1/ws/vrp/${jobId}`);
      ws.onopen = () => appendLog("WebSocket connected");
      ws.onmessage = (e) => {
        appendLog(e.data);
        try {
          const payload = JSON.parse(e.data);
          if (payload.event === "job_ready") {
            // Auto-load preview
            fetchJson(`${API_VRP}/jobs/${jobId}/preview`).then((data) => {
              const loaded = data.routes || [];
              setRoutes(loaded);
              setRouteSummaries(loaded.map((r) => ({
                fieldman_id: r.fieldman_id, distance: r.distance, duration: r.duration, tasks: r.tasks.length,
              })));
              setJobResult(JSON.stringify({ job_id: data.job_id, status: data.status }, null, 2));
              appendLog(`Auto-loaded preview for job ${jobId}`);
            }).catch(() => {});
          }
        } catch (_) {}
      };
      ws.onclose = () => appendLog("WebSocket disconnected");
      wsRef.current = ws;
    };

    const handleDisconnect = () => {
      if (wsRef.current) { wsRef.current.close(); wsRef.current = null; }
    };

    const clearLogs = () => state.setLogs([]);

    return el("div", null,
      el("div", { className: "page-header" },
        el("h2", null, "Realtime Events"),
        el("p", null, "WebSocket connection for live VRP job updates via Redis pub/sub")
      ),
      el("div", { className: "card" },
        el("div", { className: "card-header" },
          el("span", { className: "card-title" }, el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("plug") } }), "Connection"),
          el("span", { className: `pill ${wsRef.current ? "success" : "danger"}` },
            wsRef.current ? "Connected" : "Disconnected"
          )
        ),
        el("div", { className: "form-grid" },
          el("div", { className: "field span-2" },
            el("label", null, "Job ID for WebSocket"),
            el("input", { placeholder: "Enter job UUID...", value: jobId, onChange: (e) => setJobId(e.target.value) })
          )
        ),
        el("div", { className: "actions" },
          el("button", { className: "btn primary", onClick: handleConnect, disabled: !jobId }, el("span", { dangerouslySetInnerHTML: { __html: icon("plug") } }), " Connect"),
          el("button", { className: "btn ghost", onClick: handleDisconnect }, el("span", { dangerouslySetInnerHTML: { __html: icon("x") } }), " Disconnect"),
          el("button", { className: "btn ghost", onClick: clearLogs }, el("span", { dangerouslySetInnerHTML: { __html: icon("trash") } }), " Clear Logs")
        )
      ),
      el("div", { className: "card" },
        el("div", { className: "card-header" },
          el("span", { className: "card-title" }, el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("scroll") } }), "Event Log"),
          el("span", { className: "pill accent" }, `${logs.length} events`)
        ),
        el("div", { className: "log-console", style: { minHeight: "300px" } },
          logs.length === 0 ? "Waiting for events..." : logs.join("\n")
        )
      )
    );
  });

  /* ═══════════════════════════════════════════════════════════════════
     PAGE: Route Metrics
     ═══════════════════════════════════════════════════════════════════ */
  const MetricsPage = React.memo(function MetricsPage({ state }) {
    const { routeSummaries, routes, setRoutes, setRouteSummaries, jobId, setJobId, appendLog } = state;
    const [expandedRoutes, setExpandedRoutes] = useState({});
    const [loading, setLoading] = useState(false);
    const toggleRoute = (idx) => setExpandedRoutes((prev) => ({ ...prev, [idx]: !prev[idx] }));

    // Auto-load latest ready job when page mounts with no data
    useEffect(() => {
      if (routeSummaries.length > 0) return;
      (async () => {
        setLoading(true);
        try {
          const jobs = await fetchJson(`${API_VRP}/jobs?limit=50`);
          const readyJob = (Array.isArray(jobs) ? jobs : []).find((j) => (j.status || "").toLowerCase() === "ready");
          if (!readyJob) { setLoading(false); return; }
          const jid = readyJob.job_id || readyJob.id;
          const data = await fetchJson(`${API_VRP}/jobs/${jid}/preview`);
          const loadedRoutes = data.routes || [];
          setRoutes(loadedRoutes);
          setRouteSummaries(
            loadedRoutes.map((r) => ({
              fieldman_id: r.fieldman_id, distance: r.distance, duration: r.duration, tasks: (r.tasks || []).length,
            }))
          );
          setJobId(jid);
          appendLog(`Metrics: auto-loaded ${loadedRoutes.length} routes from job ${jid.substring(0, 8)}`);
        } catch (_) {}
        setLoading(false);
      })();
    }, []);

    const totalDist = routeSummaries.reduce((s, r) => s + (r.distance || 0), 0);
    const totalDur = routeSummaries.reduce((s, r) => s + (r.duration || 0), 0);
    const totalTasks = routeSummaries.reduce((s, r) => s + (r.tasks || 0), 0);
    const avgDist = routeSummaries.length > 0 ? totalDist / routeSummaries.length : 0;
    const avgDur = routeSummaries.length > 0 ? totalDur / routeSummaries.length : 0;

    return el("div", null,
      el("div", { className: "page-header" },
        el("h2", null, "Route Metrics"),
        el("p", null, "Detailed route performance analytics and statistics")
      ),
      routeSummaries.length === 0
        ? el("div", { className: "card" },
            el("div", { className: "empty-state" },
              el("div", { className: "icon", dangerouslySetInnerHTML: { __html: icon("metrics") } }),
              el("p", null, loading ? "Loading route data..." : "No route data available. Run an optimization from the Map View page first.")
            )
          )
        : el("div", null,
            el("div", { className: "stats-row" },
              el("div", { className: "stat-card" },
                el("div", { className: "stat-value" }, routeSummaries.length),
                el("div", { className: "stat-label" }, "Routes")
              ),
              el("div", { className: "stat-card" },
                el("div", { className: "stat-value" }, totalTasks),
                el("div", { className: "stat-label" }, "Total Tasks")
              ),
              el("div", { className: "stat-card" },
                el("div", { className: "stat-value" }, formatNum(totalDist / 1000, " km")),
                el("div", { className: "stat-label" }, "Total Distance")
              ),
              el("div", { className: "stat-card" },
                el("div", { className: "stat-value" }, formatNum(totalDur / 60, " min")),
                el("div", { className: "stat-label" }, "Total Duration")
              ),
              el("div", { className: "stat-card" },
                el("div", { className: "stat-value" }, formatNum(avgDist / 1000, " km")),
                el("div", { className: "stat-label" }, "Avg Distance")
              ),
              el("div", { className: "stat-card" },
                el("div", { className: "stat-value" }, formatNum(avgDur / 60, " min")),
                el("div", { className: "stat-label" }, "Avg Duration")
              )
            ),
            el("div", { className: "card" },
              el("div", { className: "card-header" },
                el("span", { className: "card-title" }, el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("dashboard") } }), "Route Breakdown")
              ),
              el("table", { className: "data-table" },
                el("thead", null,
                  el("tr", null,
                    el("th", null, "#"),
                    el("th", null, "Fieldman ID"),
                    el("th", null, "Tasks"),
                    el("th", null, "Distance (m)"),
                    el("th", null, "Duration (min)"),
                    el("th", null, "Color")
                  )
                ),
                el("tbody", null,
                  routeSummaries.map((r, i) =>
                    el("tr", { key: i },
                      el("td", null, i + 1),
                      el("td", null, r.fieldman_id),
                      el("td", null, r.tasks),
                      el("td", null, formatNum(r.distance, "")),
                      el("td", null, formatNum(r.duration / 60, "")),
                      el("td", null,
                        el("div", { className: "route-swatch", style: { background: PALETTE[i % PALETTE.length], display: "inline-block" } })
                      )
                    )
                  )
                )
              )
            ),
            el("div", { className: "card" },
              el("div", { className: "card-header" },
                el("span", { className: "card-title" }, el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("truck") } }), "Route Details"),
                el("span", { className: "pill accent" }, `${routes.length} fieldmen`)
              ),
              el("div", { className: "accordion-list" },
                routes.map((route, i) => {
                  const isOpen = !!expandedRoutes[i];
                  const tasks = (route.tasks || []).slice().sort((a, b) => a.sequence - b.sequence);
                  const color = PALETTE[i % PALETTE.length];
                  return el("div", { key: i, className: `accordion-item${isOpen ? " open" : ""}` },
                    el("div", { className: "accordion-header", onClick: () => toggleRoute(i) },
                      el("div", { className: "accordion-header-left" },
                        el("div", { className: "route-swatch", style: { background: color } }),
                        el("span", { className: "accordion-title" }, route.fieldman_id ? route.fieldman_id.substring(0, 16) : `Route ${i + 1}`),
                        el("span", { className: "pill" }, `${tasks.length} tasks`)
                      ),
                      el("div", { className: "accordion-header-right" },
                        el("span", { className: "accordion-meta" }, `${formatNum((route.distance || 0) / 1000, " km")} · ${formatNum((route.duration || 0) / 60, " min")}`),
                        el("span", { className: `accordion-chevron${isOpen ? " rotated" : ""}`, dangerouslySetInnerHTML: { __html: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>' } })
                      )
                    ),
                    isOpen && el("div", { className: "accordion-body" },
                      tasks.length === 0
                        ? el("div", { className: "accordion-empty" }, "No tasks assigned")
                        : el("table", { className: "data-table compact" },
                            el("thead", null,
                              el("tr", null,
                                el("th", null, "#"),
                                el("th", null, "Task ID"),
                                el("th", null, "Address"),
                                el("th", null, "Priority"),
                                el("th", null, "Distance"),
                                el("th", null, "Duration"),
                                el("th", null, "Service Time")
                              )
                            ),
                            el("tbody", null,
                              tasks.map((t, j) =>
                                el("tr", { key: j },
                                  el("td", null, t.sequence != null ? t.sequence : j + 1),
                                  el("td", { className: "monospace" }, t.task_id ? t.task_id.substring(0, 12) + "..." : "-"),
                                  el("td", null, t.address || "-"),
                                  el("td", null, t.priority != null ? t.priority.toFixed(1) : "-"),
                                  el("td", null, formatNum((t.distance || 0) / 1000, " km")),
                                  el("td", null, formatNum((t.duration || 0) / 60, " min")),
                                  el("td", null, t.service != null ? formatNum((t.service || 0) / 60, " min") : "-")
                                )
                              )
                            )
                          )
                    )
                  );
                })
              )
            )
          )
    );
  });

  /* ═══════════════════════════════════════════════════════════════════
     PAGE: Fieldman Tasks (with preview map)
     ═══════════════════════════════════════════════════════════════════ */
  const FieldmanTasksPage = React.memo(function FieldmanTasksPage({ state }) {
    const { overview, appendLog, routes, setRoutes, setRouteSummaries, jobId } = state;
    const [selectedFm, setSelectedFm] = useState("");
    const [tasks, setTasks] = useState([]);
    const [stats, setStats] = useState({ total: 0, pending: 0, completed: 0 });
    const [loading, setLoading] = useState(false);
    const [completing, setCompleting] = useState(null); // task_id being completed
    const [message, setMessage] = useState("");
    const [highlightTask, setHighlightTask] = useState(null); // task_id hovered in list

    // ── Preview map refs ──────────────────────────────────────────
    const mapContainerRef = useRef(null);
    const mapInstanceRef = useRef(null);   // { map, taskLayer, fmLayer }
    const markersRef = useRef({});          // task_id -> marker

    // Load fieldman tasks
    const loadTasks = useCallback(async (fmId) => {
      if (!fmId) { setTasks([]); setStats({ total: 0, pending: 0, completed: 0 }); return; }
      setLoading(true);
      setMessage("");
      try {
        const data = await fetchJson(`${API_VRP}/fieldmen/${fmId}/tasks`);
        setTasks(data.tasks || []);
        setStats({ total: data.total || 0, pending: data.pending || 0, completed: data.completed || 0 });
        if (data.message) setMessage(data.message);
      } catch (err) {
        showToast(`Failed to load tasks: ${err.message}`, "error");
        setTasks([]);
        setStats({ total: 0, pending: 0, completed: 0 });
      }
      setLoading(false);
    }, []);

    // When fieldman selection changes
    useEffect(() => {
      if (selectedFm) loadTasks(selectedFm);
      else { setTasks([]); setStats({ total: 0, pending: 0, completed: 0 }); }
    }, [selectedFm, loadTasks]);

    // Complete a task
    const handleComplete = useCallback(async (taskId) => {
      if (completing) return;
      setCompleting(taskId);
      try {
        const data = await fetchJson(`${API_VRP}/fieldmen/${selectedFm}/tasks/${taskId}/complete`, { method: "POST" });
        setTasks(data.tasks || []);
        setStats({ total: (data.tasks || []).length, pending: data.pending || 0, completed: data.completed || 0 });
        const msg = data.message || "Task completed";
        setMessage(msg);
        showToast(msg, "success");
        appendLog(`Task ${taskId.substring(0, 8)} completed for FM ${selectedFm.substring(0, 8)}`);
        invalidateCache("fieldmen");
        invalidateCache("preview");

        // Refresh routes in shared state so Map View / Preview updates live
        if (jobId && routes.length > 0) {
          try {
            const preview = await fetchJson(`${API_VRP}/jobs/${jobId}/preview`);
            const lr = preview.routes || [];
            setRoutes(lr);
            setRouteSummaries(lr.map(r => ({
              fieldman_id: r.fieldman_id, distance: r.distance, duration: r.duration, tasks: r.tasks.length,
            })));
          } catch (_) { /* silently ignore preview refresh failure */ }
        }
      } catch (err) {
        const errMsg = err.message || "Failed to complete task";
        showToast(errMsg, "error");
      }
      setCompleting(null);
    }, [selectedFm, completing, appendLog, jobId, routes, setRoutes, setRouteSummaries]);

    // ── Initialize Leaflet map once container is available ──────
    useEffect(() => {
      if (!mapContainerRef.current || mapInstanceRef.current || !L) return;
      let destroyed = false;
      const map = L.map(mapContainerRef.current, { zoomControl: false, preferCanvas: true });
      const pane = map.getPane("mapPane");
      if (pane && !pane._leaflet_pos) L.DomUtil.setPosition(pane, L.point(0, 0));
      map.setView([14.5995, 120.9842], 11);
      L.control.zoom({ position: "bottomright" }).addTo(map);

      const tiles = L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
        attribution: "&copy; OpenStreetMap &copy; CARTO", maxZoom: 19, subdomains: "abcd",
      });
      let tileErr = 0, usingFallback = false;
      tiles.on("tileerror", () => {
        tileErr++;
        if (!usingFallback && tileErr >= 3) {
          usingFallback = true;
          map.removeLayer(tiles);
          L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            attribution: "&copy; OpenStreetMap", maxZoom: 19,
          }).addTo(map);
        }
      });
      tiles.addTo(map);

      const routeLayer = L.layerGroup().addTo(map);
      const taskLayer = L.layerGroup().addTo(map);
      const fmLayer = L.layerGroup().addTo(map);

      mapInstanceRef.current = { map, routeLayer, taskLayer, fmLayer };
      // Multiple invalidateSize calls to ensure tiles render after container appears
      setTimeout(() => { if (!destroyed) map.invalidateSize(); }, 100);
      setTimeout(() => { if (!destroyed) map.invalidateSize(); }, 400);
      setTimeout(() => { if (!destroyed) map.invalidateSize(); }, 800);

      const onResize = () => { if (!destroyed && mapInstanceRef.current) mapInstanceRef.current.map.invalidateSize(); };
      window.addEventListener("resize", onResize);

      return () => {
        destroyed = true;
        window.removeEventListener("resize", onResize);
        if (mapInstanceRef.current) { mapInstanceRef.current.map.remove(); mapInstanceRef.current = null; }
      };
    }, [selectedFm]);

    // ── Draw / update markers whenever tasks or selectedFm change ─
    useEffect(() => {
      if (!mapInstanceRef.current) return;
      const { map, routeLayer, taskLayer, fmLayer } = mapInstanceRef.current;

      // Clear old layers
      routeLayer.clearLayers();
      taskLayer.clearLayers();
      fmLayer.clearLayers();
      markersRef.current = {};

      const bounds = [];

      // ── Draw route polyline from shared routes state ────────────
      const fmRoute = (routes || []).find(r => r.fieldman_id === selectedFm);
      if (fmRoute && fmRoute.geometry && fmRoute.geometry.type === "LineString") {
        const rawCoords = Array.isArray(fmRoute.geometry.coordinates) ? fmRoute.geometry.coordinates : [];
        const latLngs = rawCoords.map(c => [c[1], c[0]]);
        if (latLngs.length > 1) {
          L.polyline(latLngs, {
            color: fmRoute.color || "#6366f1", weight: 3, opacity: 0.7,
            lineCap: "round", lineJoin: "round",
          }).addTo(routeLayer);
          latLngs.forEach(ll => bounds.push(ll));
        }
      }

      // Determine fieldman current position: last completed task, or home if none done
      const fmObj = (overview.fieldmen || []).find(f => f.fieldman_id === selectedFm);
      const completedTasks = tasks.filter(t => t.status === "completed" && t.latitude != null);
      const lastCompleted = completedTasks.length > 0
        ? completedTasks[completedTasks.length - 1]
        : null;

      // Fieldman current position marker
      const fmLat = lastCompleted ? lastCompleted.latitude : (fmObj ? fmObj.latitude : null);
      const fmLng = lastCompleted ? lastCompleted.longitude : (fmObj ? fmObj.longitude : null);
      const fmLabel = lastCompleted
        ? `Fieldman @ task #${lastCompleted.sequence || "?"}: ${lastCompleted.address || "Unknown"}`
        : `Fieldman home: ${fmObj ? (fmObj.address || fmObj.fieldman_id.substring(0, 12)) : ""}`;

      if (fmLat != null && fmLng != null) {
        const fmIcon = L.divIcon({
          className: "vrp-fieldman-marker",
          html: `<div style="background:#ef4444;color:#fff;border:2px solid #fff;border-radius:50%;width:32px;height:32px;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 6px rgba(0,0,0,0.35);z-index:800;">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="4" cy="19" r="3"/><circle cx="19" cy="19" r="3"/><path d="M12 19V9l-3 3"/><path d="M9 12h6l3-6h-4l-2-3H7"/></svg>
          </div>`,
          iconSize: [32, 32],
          iconAnchor: [16, 16],
        });
        L.marker([fmLat, fmLng], { icon: fmIcon, zIndexOffset: 1000 })
          .bindTooltip(fmLabel, { direction: "auto", offset: [0, -18], className: "preview-tt" })
          .addTo(fmLayer);
        bounds.push([fmLat, fmLng]);
      }

      // Also show home as small muted marker if FM has moved away from home
      if (lastCompleted && fmObj && fmObj.latitude != null && fmObj.longitude != null) {
        const homeIcon = L.divIcon({
          className: "vrp-fieldman-home",
          html: `<div style="background:rgba(239,68,68,0.3);color:#ef4444;border:2px dashed #ef4444;border-radius:50%;width:22px;height:22px;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;">H</div>`,
          iconSize: [22, 22],
          iconAnchor: [11, 11],
        });
        L.marker([fmObj.latitude, fmObj.longitude], { icon: homeIcon, zIndexOffset: 500 })
          .bindTooltip(`Home: ${fmObj.address || fmObj.fieldman_id.substring(0, 12)}`, { direction: "auto", offset: [0, -12], className: "preview-tt" })
          .addTo(fmLayer);
        bounds.push([fmObj.latitude, fmObj.longitude]);
      }

      // Task markers
      tasks.forEach((t) => {
        if (t.latitude == null || t.longitude == null) return;
        const isCompleted = t.status === "completed";
        const typeColor = TASK_TYPE_COLORS[t.task_type] || "#6366f1";
        const markerColor = isCompleted ? "#22c55e" : typeColor;
        const label = isCompleted ? "✓" : (t.sequence != null ? t.sequence : "•");
        const opacity = isCompleted ? 0.65 : 1;

        const taskIcon = L.divIcon({
          className: "fmtask-map-marker",
          html: `<div style="background:${markerColor};color:#fff;border:2px solid #fff;border-radius:50%;width:26px;height:26px;display:flex;align-items:center;justify-content:center;font-size:${isCompleted ? "14" : "11"}px;font-weight:700;box-shadow:0 1px 4px rgba(0,0,0,0.3);opacity:${opacity};">${label}</div>`,
          iconSize: [26, 26],
          iconAnchor: [13, 13],
        });

        const typeShort = TASK_TYPE_SHORT[t.task_type] || "TASK";
        const ttHtml = `<div style="font-size:12px;line-height:1.4;">
          <b style="color:${markerColor}">${typeShort}</b> — ${t.address || "Unknown"}
          <br>Status: <b>${isCompleted ? "Completed ✓" : "Pending"}</b>
          ${t.sequence != null && !isCompleted ? "<br>Sequence: #" + t.sequence : ""}
          ${t.distance != null ? "<br>Distance: " + (t.distance / 1000).toFixed(2) + " km" : ""}
          ${t.duration != null ? "<br>Duration: " + Math.round(t.duration / 60) + " min" : ""}
          ${isCompleted && t.completed_at ? "<br>Done: " + new Date(t.completed_at).toLocaleString() : ""}
        </div>`;

        const marker = L.marker([t.latitude, t.longitude], { icon: taskIcon, zIndexOffset: isCompleted ? 400 : 600 })
          .bindTooltip(ttHtml, { direction: "auto", offset: [0, -14], className: "preview-tt" })
          .addTo(taskLayer);

        markersRef.current[t.task_id] = marker;
        bounds.push([t.latitude, t.longitude]);
      });

      // Fit bounds
      if (bounds.length > 0) {
        try {
          map.fitBounds(bounds, { padding: [40, 40], maxZoom: 15 });
        } catch (_) { /* ignore invalid bounds */ }
      }

      // Invalidate size after layout settle
      setTimeout(() => { if (mapInstanceRef.current) mapInstanceRef.current.map.invalidateSize(); }, 200);
    }, [tasks, selectedFm, overview.fieldmen, routes]);

    // ── Highlight marker on list hover ────────────────────────────
    useEffect(() => {
      if (!mapInstanceRef.current) return;
      Object.entries(markersRef.current).forEach(([tid, marker]) => {
        const el = marker.getElement();
        if (!el) return;
        const inner = el.querySelector("div");
        if (!inner) return;
        if (tid === highlightTask) {
          inner.style.transform = "scale(1.4)";
          inner.style.boxShadow = "0 0 12px rgba(99,102,241,0.7)";
          inner.style.zIndex = "9999";
          marker.openTooltip();
        } else {
          inner.style.transform = "";
          inner.style.boxShadow = "0 1px 4px rgba(0,0,0,0.3)";
          inner.style.zIndex = "";
        }
      });
    }, [highlightTask]);

    const fieldmen = overview.fieldmen || [];

    return el("div", null,
      el("div", { className: "page-header" },
        el("h2", null, "Fieldman Tasks"),
        el("p", null, "View and manage task assignments per fieldman. Mark tasks as completed.")
      ),

      // Fieldman selector
      el("div", { className: "card" },
        el("div", { className: "card-header" },
          el("span", { className: "card-title" },
            el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("motorcycle") } }),
            "Select Fieldman"
          )
        ),
        el("div", { style: { padding: "16px" } },
          el("div", { className: "field" },
            el("label", null, "Fieldman"),
            el("select", {
              value: selectedFm,
              onChange: (e) => setSelectedFm(e.target.value),
              style: { width: "100%", padding: "8px 12px", borderRadius: "8px", border: "1px solid rgba(0,0,0,0.15)", fontSize: "13px", fontFamily: "'JetBrains Mono', monospace" },
            },
              el("option", { value: "" }, "— Choose a fieldman —"),
              fieldmen.map((fm) =>
                el("option", { key: fm.fieldman_id, value: fm.fieldman_id },
                  `${fm.fieldman_id.substring(0, 12)}... — ${fm.address || "No address"}`
                )
              )
            )
          ),
          selectedFm && el("button", {
            className: "btn secondary sm",
            style: { marginTop: "8px" },
            onClick: () => loadTasks(selectedFm),
          }, el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("refresh") } }), " Refresh")
        )
      ),

      // Stats row
      selectedFm && el("div", { className: "stats-row", style: { marginTop: "16px" } },
        el("div", { className: "stat-card" },
          el("div", { className: "stat-value" }, stats.total),
          el("div", { className: "stat-label" }, "Total Tasks")
        ),
        el("div", { className: "stat-card", style: { borderLeft: "4px solid var(--accent)" } },
          el("div", { className: "stat-value", style: { color: "var(--accent)" } }, stats.pending),
          el("div", { className: "stat-label" }, "Pending")
        ),
        el("div", { className: "stat-card", style: { borderLeft: "4px solid var(--success)" } },
          el("div", { className: "stat-value", style: { color: "var(--success)" } }, stats.completed),
          el("div", { className: "stat-label" }, "Completed")
        )
      ),

      // Message banner
      message && el("div", {
        className: "fm-tasks-message",
        style: { margin: "16px 0", padding: "10px 16px", borderRadius: "8px", background: "var(--bg-elevated)", color: "var(--text-secondary)", fontSize: "13px", fontWeight: 500 },
      }, message),

      // ── Preview Map ─────────────────────────────────────────────
      selectedFm && el("div", { className: "card fm-preview-map-card", style: { marginTop: "16px" } },
        el("div", { className: "card-header" },
          el("span", { className: "card-title" },
            el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("map") } }),
            "Task Map Preview"
          ),
          tasks.length > 0 && el("span", { className: "pill accent" },
            `${stats.pending} pending · ${stats.completed} done`
          )
        ),
        el("div", {
          ref: mapContainerRef,
          className: "fm-preview-map",
          style: { height: "400px", width: "100%", borderRadius: "0 0 12px 12px", background: "var(--bg-elevated)" },
        }),
        !selectedFm && el("div", { className: "empty-state", style: { height: "400px" } },
          el("p", null, "Select a fieldman to see their tasks on the map.")
        )
      ),

      // ── Legend ───────────────────────────────────────────────────
      selectedFm && tasks.length > 0 && el("div", { className: "fm-map-legend", style: { marginTop: "8px", display: "flex", gap: "16px", flexWrap: "wrap", fontSize: "12px", color: "var(--text-secondary)" } },
        el("span", null,
          el("span", { style: { display: "inline-block", width: "12px", height: "12px", borderRadius: "50%", background: "#ef4444", marginRight: "4px", verticalAlign: "middle" } }),
          "Fieldman Position"
        ),
        el("span", null,
          el("span", { style: { display: "inline-block", width: "12px", height: "12px", borderRadius: "50%", background: "rgba(239,68,68,0.3)", border: "2px dashed #ef4444", marginRight: "4px", verticalAlign: "middle", boxSizing: "border-box" } }),
          "Home Base"
        ),
        el("span", null,
          el("span", { style: { display: "inline-block", width: "12px", height: "12px", borderRadius: "50%", background: "#6366f1", marginRight: "4px", verticalAlign: "middle" } }),
          "Pending Task"
        ),
        el("span", null,
          el("span", { style: { display: "inline-block", width: "12px", height: "12px", borderRadius: "50%", background: "#22c55e", marginRight: "4px", verticalAlign: "middle" } }),
          "Completed Task"
        ),
        el("span", null,
          el("span", { style: { display: "inline-block", width: "16px", height: "3px", borderRadius: "2px", background: "#6366f1", marginRight: "4px", verticalAlign: "middle" } }),
          "Route"
        )
      ),

      // Task list
      selectedFm && !loading && tasks.length === 0 &&
        el("div", { className: "card", style: { marginTop: "16px" } },
          el("div", { className: "empty-state" },
            el("div", { className: "icon", dangerouslySetInnerHTML: { __html: icon("tasks") } }),
            el("p", null, "No tasks assigned to this fieldman.")
          )
        ),

      selectedFm && loading &&
        el("div", { className: "card", style: { marginTop: "16px" } },
          el("div", { className: "empty-state" },
            el("div", { className: "loading-spinner" }),
            el("p", null, "Loading tasks...")
          )
        ),

      selectedFm && !loading && tasks.length > 0 &&
        el("div", { className: "card", style: { marginTop: "16px" } },
          el("div", { className: "card-header" },
            el("span", { className: "card-title" },
              el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("clipboard") } }),
              "Task List"
            ),
            el("span", { className: "pill accent" }, `${tasks.length} tasks`)
          ),
          el("div", { className: "fm-task-list" },
            tasks.map((t, i) => {
              const isCompleted = t.status === "completed";
              const isCompleting = completing === t.task_id;
              const typeShort = TASK_TYPE_SHORT[t.task_type] || "TASK";
              const typeColor = TASK_TYPE_COLORS[t.task_type] || "#6366f1";
              return el("div", {
                key: t.task_id + "-" + i,
                className: `fm-task-item ${isCompleted ? "fm-task-completed" : "fm-task-pending"} ${highlightTask === t.task_id ? "fm-task-highlight" : ""}`,
                onMouseEnter: () => setHighlightTask(t.task_id),
                onMouseLeave: () => setHighlightTask(null),
              },
                el("div", { className: "fm-task-seq" },
                  isCompleted
                    ? el("span", { className: "fm-task-check", style: { display: "flex", alignItems: "center", gap: "2px" } },
                        el("span", { dangerouslySetInnerHTML: { __html: icon("check") } }),
                        t.sequence != null && el("span", { style: { fontSize: "9px", opacity: 0.7 } }, t.sequence)
                      )
                    : el("span", { className: "fm-task-num" }, t.sequence != null ? t.sequence : "-")
                ),
                el("div", { className: "fm-task-info" },
                  el("div", { className: "fm-task-header" },
                    el("span", { className: "fm-task-type-badge", style: { background: typeColor } }, typeShort),
                    el("span", { className: "fm-task-address" }, t.address || "Unknown address"),
                    t.bank && el("span", { className: "pill", style: { marginLeft: "6px", fontSize: "10px" } }, t.bank)
                  ),
                  el("div", { className: "fm-task-meta" },
                    el("span", null, `Priority: ${t.manual_priority != null ? "★ " + t.manual_priority : (t.priority != null ? t.priority.toFixed(1) : "-")}`),
                    t.distance != null && el("span", null, `${(t.distance / 1000).toFixed(2)} km`),
                    t.duration != null && el("span", null, `${Math.round(t.duration / 60)} min`),
                    t.service != null && t.service > 0 && el("span", null, `Svc: ${Math.round(t.service / 60)} min`)
                  ),
                  isCompleted && t.completed_at && el("div", { className: "fm-task-completed-at" },
                    `Completed: ${new Date(t.completed_at).toLocaleString()}`
                  )
                ),
                el("div", { className: "fm-task-actions" },
                  !isCompleted && el("button", {
                    className: `btn success sm ${isCompleting ? "btn-loading" : ""}`,
                    disabled: isCompleting || !!completing,
                    onClick: () => handleComplete(t.task_id),
                    title: "Mark as completed",
                  },
                    isCompleting
                      ? el("span", { className: "loading-spinner-sm" })
                      : el("span", null,
                          el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("check") } }),
                          " Done"
                        )
                  ),
                  isCompleted && el("span", { className: "pill success", style: { fontSize: "11px" } }, "Completed")
                )
              );
            })
          )
        )
    );
  });

  /* ═══════════════════════════════════════════════════════════════════
     PAGE: System Health
     ═══════════════════════════════════════════════════════════════════ */
  const HealthPage = React.memo(function HealthPage({ state }) {
    const { statusOk } = state;
    const [healthData, setHealthData] = useState(null);
    const [deepHealth, setDeepHealth] = useState(null);
    const [loading, setLoading] = useState(false);

    const checkHealth = async () => {
      setLoading(true);
      try {
        // Basic health (fast, <2s)
        const controller1 = new AbortController();
        const t1 = setTimeout(() => controller1.abort(), 8000);
        const data = await fetchJson(API_HEALTH, { signal: controller1.signal });
        clearTimeout(t1);
        setHealthData(data);
      } catch (err) {
        const msg = err.name === "AbortError" || (err.message && err.message.includes("abort"))
          ? "API Server unreachable (timeout)" : err.message;
        setHealthData({ error: msg });
      }
      try {
        // Deep health — checks all services individually
        const controller2 = new AbortController();
        const t2 = setTimeout(() => controller2.abort(), 15000);
        const deep = await fetchJson(`${API_V1}/health/deep`, { signal: controller2.signal });
        clearTimeout(t2);
        setDeepHealth(deep && deep.data ? deep.data : deep);
      } catch (err) {
        const msg = err.name === "AbortError" || (err.message && err.message.includes("abort"))
          ? "API Server unreachable (timeout)" : err.message;
        setDeepHealth({ error: msg });
      }
      setLoading(false);
    };

    useEffect(() => { checkHealth(); }, []);

    const svcStatus = (key) => deepHealth && deepHealth[key] === "ok" ? "ok" : deepHealth && deepHealth[key] === "error" ? "err" : statusOk ? "ok" : "loading";
    const svcLabel = (key, label) => deepHealth && deepHealth[key] === "ok" ? `${label} — healthy` : deepHealth && deepHealth[key] === "error" ? `${label} — error` : deepHealth && deepHealth[key] === "degraded" ? `${label} — degraded` : statusOk ? label : "Checking...";

    return el("div", null,
      el("div", { className: "page-header" },
        el("h2", null, "System Health"),
        el("p", null, "Monitor service status and infrastructure health")
      ),
      el("div", { className: "card" },
        el("div", { className: "card-header" },
          el("span", { className: "card-title" }, el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("health") } }), "Service Status"),
          el("button", { className: "btn ghost sm", onClick: checkHealth, disabled: loading }, el("span", { dangerouslySetInnerHTML: { __html: icon("refresh") } }), " Refresh")
        ),
        el("div", { className: "health-grid" },
          el("div", { className: "health-item" },
            el("div", { className: `health-dot ${statusOk ? "ok" : "err"}` }),
            el("div", null,
              el("div", { className: "health-label" }, "API Server"),
              el("div", { className: "health-sub" }, statusOk ? "Healthy — responding" : "Unreachable")
            )
          ),
          el("div", { className: "health-item" },
            el("div", { className: `health-dot ${svcStatus("db")}` }),
            el("div", null,
              el("div", { className: "health-label" }, "PostgreSQL"),
              el("div", { className: "health-sub" }, svcLabel("db", "Connected"))
            )
          ),
          el("div", { className: "health-item" },
            el("div", { className: `health-dot ${svcStatus("redis")}` }),
            el("div", null,
              el("div", { className: "health-label" }, "Redis"),
              el("div", { className: "health-sub" }, svcLabel("redis", "Available"))
            )
          ),
          el("div", { className: "health-item" },
            el("div", { className: `health-dot ${svcStatus("osrm")}` }),
            el("div", null,
              el("div", { className: "health-label" }, "OSRM"),
              el("div", { className: "health-sub" }, svcLabel("osrm", "Road network engine"))
            )
          ),
          el("div", { className: "health-item" },
            el("div", { className: `health-dot ${svcStatus("vroom")}` }),
            el("div", null,
              el("div", { className: "health-label" }, "VROOM"),
              el("div", { className: "health-sub" }, svcLabel("vroom", "VRP solver"))
            )
          ),
          el("div", { className: "health-item" },
            el("div", { className: `health-dot ${statusOk ? "ok" : "loading"}` }),
            el("div", null,
              el("div", { className: "health-label" }, "Celery Workers"),
              el("div", { className: "health-sub" }, "Async task processing")
            )
          )
        )
      ),
      (healthData || deepHealth) && el("div", { className: "card" },
        el("div", { className: "card-header" },
          el("span", { className: "card-title" }, el("span", { className: "icon", dangerouslySetInnerHTML: { __html: icon("clipboard") } }), "Raw Health Response")
        ),
        el("div", { className: "output-box" }, JSON.stringify(deepHealth || healthData, null, 2))
      )
    );
  });

  /* ═══════════════════════════════════════════════════════════════════
     ROOT APP
     ═══════════════════════════════════════════════════════════════════ */
  function App() {
    const state = useAppState();
    const { page, setPage, setStatusOk, setStatusText, setOverview, setTaskSummary, setRoutes, setRouteSummaries, setJobId, appendLog } = state;
    const pageVisible = usePageVisible();

    // Load persisted data from DB on mount
    useEffect(() => {
      // 1) Load overview (tasks + fieldmen markers) — cached
      cachedFetch(`${API_VRP}/overview`, { ttlMs: 10000 })
        .then(o => { if (o) setOverview(o); })
        .catch(() => {});
      // 2) Load task summary counts — cached
      cachedFetch(`${API_VRP}/task-summary`, { ttlMs: 10000 })
        .then(d => { if (d) setTaskSummary(d); })
        .catch(() => {});
      // 3) Load latest completed job routes (if any)
      cachedFetch(`${API_VRP}/jobs?status=completed&limit=1`, { ttlMs: 15000 })
        .then(async (jobs) => {
          if (!jobs || !jobs.length) return;
          const latestJob = jobs[0];
          setJobId(latestJob.job_id || latestJob.id);
          try {
            const preview = await cachedFetch(`${API_VRP}/jobs/${latestJob.job_id || latestJob.id}/preview`, { ttlMs: 30000 });
            const loadedRoutes = preview.routes || [];
            if (loadedRoutes.length) {
              setRoutes(loadedRoutes);
              setRouteSummaries(
                loadedRoutes.map(r => ({
                  fieldman_id: r.fieldman_id, distance: r.distance, duration: r.duration, tasks: r.tasks.length,
                }))
              );
            }
          } catch (_) {}
        })
        .catch(() => {});
    }, []);

    // Refresh overview + summary when tab becomes visible (picks up external resets)
    useEffect(() => {
      if (!pageVisible) return;
      invalidateCache("overview");
      invalidateCache("task-summary");
      invalidateCache("jobs");
      fetchJson(`${API_VRP}/overview`).then(o => {
        if (o) {
          setOverview(o);
          // If overview is empty (data was reset externally), clear persisted routes/jobs
          if ((!o.tasks || !o.tasks.length) && (!o.fieldmen || !o.fieldmen.length)) {
            setRoutes([]);
            setRouteSummaries([]);
            state.setJobId("");
            try { localStorage.removeItem("vrp:routes"); localStorage.removeItem("vrp:routeSummaries"); localStorage.removeItem("vrp:jobId"); } catch(_){}
          }
        }
      }).catch(() => {});
      fetchJson(`${API_VRP}/task-summary`).then(d => { if (d) setTaskSummary(d); }).catch(() => {});
    }, [pageVisible]);

    // Health check polling — visibility-aware (pauses when tab is hidden)
    useEffect(() => {
      let active = true;
      const check = async () => {
        if (document.hidden) return; // Skip when tab not visible
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 10000);
        try {
          const resp = await fetch(API_HEALTH, { signal: controller.signal });
          const data = await resp.json();
          if (!active) return;
          const ok = resp.ok && data && (data.db === "ok" || (data.data && data.data.db === "ok"));
          setStatusOk(ok);
          setStatusText(ok ? "API OK · DB OK" : data && data.db === "error" ? "DB DOWN" : "Degraded");
        } catch (err) {
          if (!active) return;
          setStatusOk(false);
          setStatusText(err && err.name === "AbortError" ? "Health check timed out" : "API Unreachable");
        } finally {
          clearTimeout(timer);
        }
      };
      check();
      const iv = setInterval(check, 30000);  // 30s — reduced from 8s to minimize re-renders
      return () => { active = false; clearInterval(iv); };
    }, []);

    // Page router
    let pageContent = null;
    switch (page) {
      case "dashboard": pageContent = el(DashboardPage, { state }); break;
      case "jobs": pageContent = el(JobsPage, { state }); break;
      case "map": pageContent = el(MapViewPage, { state }); break;
      case "realtime": pageContent = el(RealtimePage, { state }); break;
      case "metrics": pageContent = el(MetricsPage, { state }); break;
      case "fieldman_tasks": pageContent = el(FieldmanTasksPage, { state }); break;
      case "health": pageContent = el(HealthPage, { state }); break;
      default: pageContent = el(DashboardPage, { state });
    }

    return el("div", { className: "app-shell" },
      el(Topbar, { statusOk: state.statusOk, statusText: state.statusText }),
      el(Sidebar, {
        activePage: page,
        onNavigate: setPage,
        routeCount: state.routeSummaries.length,
        logCount: state.logs.length,
      }),
      el("main", { className: "main-content" }, pageContent)
    );
  }

  const root = ReactDOM.createRoot(document.getElementById("root"));
  root.render(el(App));
})();

