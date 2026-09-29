/* Motion kit — shared helpers for every studio motion template.
 * Everything is built with the Web Animations API (element.animate) so the virtual clock can seek any
 * frame exactly. Times are in SECONDS. All sizes scale with --u (1u = 1px at 1080p short side). */
const P = window.PARAMS || {};
const S = document.getElementById('stage');
const W = window.innerWidth, H = window.innerHeight;
const U = Math.min(W, H) / 1080;
const PORTRAIT = H > W * 1.05, SQUAREISH = !PORTRAIT && W < H * 1.3;
const C = Object.assign({primary: '#FF5A36', accent: '#FFC53D', background: '#0E0F1A', text: '#FFFFFF',
                         muted: 'rgba(255,255,255,0.72)', panel: '#15172A', on_primary: '#FFFFFF'}, P.colors || {});
const EASE = {
  out: 'cubic-bezier(0.16, 1, 0.3, 1)',        // expo-out: fast start, long soft landing
  out3: 'cubic-bezier(0.33, 1, 0.68, 1)',
  in: 'cubic-bezier(0.7, 0, 0.84, 0)',
  inOut: 'cubic-bezier(0.87, 0, 0.13, 1)',     // expo-in-out, for wipes
  inOut3: 'cubic-bezier(0.65, 0, 0.35, 1)',
  back: 'cubic-bezier(0.34, 1.56, 0.64, 1)',
  linear: 'linear',
};
const RTL_RE = /[֐-ࣿיִ-﷿ﹰ-﻿]/;
const isRTL = (s) => RTL_RE.test(String(s || ''));
const TEXT_ALL = [P.title, P.subtitle, P.text, P.name, P.role, P.kicker, P.label, P.tagline].filter(Boolean).join(' ');
const RTL = P.rtl != null ? !!P.rtl : isRTL(TEXT_ALL);
document.documentElement.style.setProperty('--u', U + 'px');
document.documentElement.dir = RTL ? 'rtl' : 'ltr';
for (const [k, v] of Object.entries(C)) document.documentElement.style.setProperty('--' + k, v);
if (P.transparent) document.body.classList.add('alpha');
else document.body.style.background = C.background;

function el(tag, cls, parent, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  (parent === undefined ? S : parent)?.appendChild(n);
  return n;
}
/** animate: node, keyframes, start (s), duration (s), easing name, extra options */
function A(node, kf, t, d, ease = 'out', opt = {}) {
  return node.animate(kf, Object.assign({delay: t * 1000, duration: Math.max(1, d * 1000),
    easing: EASE[ease] || ease, fill: 'both'}, opt));
}
/** Split text into words; each word = mask (overflow hidden) > inner span. Arabic stays word-level
 *  so letters keep their joined shapes. Returns [{mask, inner, word}]. */
function words(parent, text, cls = '') {
  const out = [];
  const parts = String(text).split(/(\s+)/);
  for (const p of parts) {
    if (!p) continue;
    if (/^\s+$/.test(p)) { parent.appendChild(document.createTextNode(' ')); continue; }
    let emph = false, w = p;
    const m = /^\*(.+)\*([.,!?،؟:;]*)$/.exec(p);
    if (m) { emph = true; w = m[1] + m[2]; }
    const mask = el('span', 'wmask ' + cls + (emph ? ' emph' : ''), parent);
    const inner = el('span', 'winner', mask, w);
    out.push({mask, inner, word: w, emph});
  }
  return out;
}
/** Latin: per-character spans; Arabic/Hebrew: falls back to words (never break joining). */
function chars(parent, text, cls = '') {
  if (isRTL(text)) return words(parent, text, cls).map(o => ({mask: o.mask, inner: o.inner}));
  const out = [];
  for (const w of String(text).split(/(\s+)/)) {
    if (!w) continue;
    if (/^\s+$/.test(w)) { parent.appendChild(document.createTextNode(' ')); continue; }
    const grp = el('span', 'wgroup', parent);
    for (const ch of w) { const mask = el('span', 'cmask ' + cls, grp); out.push({mask, inner: el('span', 'cinner', mask, ch)}); }
  }
  return out;
}
/** Shrink font-size until the node fits maxW × maxH (px). */
function fit(node, maxW, maxH = 1e9, minPx = 12) {
  let fs = parseFloat(getComputedStyle(node).fontSize);
  for (let i = 0; i < 60 && fs > minPx && (node.scrollWidth > maxW + 1 || node.scrollHeight > maxH + 1); i++) {
    fs *= 0.95; node.style.fontSize = fs + 'px';
  }
  return fs;
}
function setDuration(d) { window.__duration = (P.duration && P.duration > 0) ? P.duration : d; return window.__duration; }
function digits(s) {
  if (P.digits !== 'arabic') return String(s);
  return String(s).replace(/[0-9]/g, (d) => '٠١٢٣٤٥٦٧٨٩'[+d]).replace(/,/g, '٬').replace(/\./g, '٫');
}
function fmtNum(v, dec = 0) {
  const s = Number(v).toLocaleString('en-US', {minimumFractionDigits: dec, maximumFractionDigits: dec});
  return digits(s);
}
let __grainURL = null;
function grainLayer(parent) {
  if (!__grainURL) {
    const cv = document.createElement('canvas'); cv.width = cv.height = 256;
    const g = cv.getContext('2d'); const img = g.createImageData(256, 256);
    let seed = 1337; const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);
    for (let i = 0; i < img.data.length; i += 4) { const v = rnd() * 255; img.data[i] = img.data[i + 1] = img.data[i + 2] = v; img.data[i + 3] = 255; }
    g.putImageData(img, 0, 0); __grainURL = cv.toDataURL('image/png');
  }
  const d = el('div', 'grain', parent); d.style.backgroundImage = `url(${__grainURL})`; return d;
}
/** Soft animated gradient background with optional grain; returns the layer. */
function backdrop(opts = {}) {
  if (P.transparent) return null;
  const bg = el('div', 'bd');
  const b1 = el('div', 'blob', bg), b2 = el('div', 'blob', bg);
  b1.style.background = `radial-gradient(closest-side, ${opts.c1 || C.primary}, transparent)`;
  b2.style.background = `radial-gradient(closest-side, ${opts.c2 || C.accent}, transparent)`;
  Object.assign(b1.style, {width: 1400 * U + 'px', height: 1400 * U + 'px', left: -300 * U + 'px', top: -500 * U + 'px', opacity: opts.o1 ?? 0.32});
  Object.assign(b2.style, {width: 1200 * U + 'px', height: 1200 * U + 'px', right: -400 * U + 'px', bottom: -600 * U + 'px', opacity: opts.o2 ?? 0.2});
  const T = opts.dur || 8;
  A(b1, [{transform: 'translate(0,0) scale(1)'}, {transform: `translate(${160 * U}px, ${90 * U}px) scale(1.12)`}], 0, T, 'inOut3');
  A(b2, [{transform: 'translate(0,0) scale(1.05)'}, {transform: `translate(${-140 * U}px, ${-60 * U}px) scale(0.95)`}], 0, T, 'inOut3');
  if (opts.grain !== false) window.__grain = true;  // static grain added by ffmpeg at encode time (cheap, compresses well)
  if (opts.vignette !== false) el('div', 'vignette', bg);
  return bg;
}
const ICONS = {
  bell: '<svg viewBox="0 0 24 24"><path d="M12 22a2.5 2.5 0 0 0 2.45-2h-4.9A2.5 2.5 0 0 0 12 22Zm7-6V11a7 7 0 0 0-5.5-6.84V3.5a1.5 1.5 0 0 0-3 0v.66A7 7 0 0 0 5 11v5l-2 2v1h18v-1Z" fill="currentColor"/></svg>',
  thumb: '<svg viewBox="0 0 24 24"><path d="M2 21h4V9H2v12Zm20-11a2 2 0 0 0-2-2h-6.31l.95-4.57.03-.32a1.5 1.5 0 0 0-.44-1.06L13.17 1 6.59 7.59A2 2 0 0 0 6 9v10a2 2 0 0 0 2 2h9a2 2 0 0 0 1.84-1.22l3.02-7.05A2 2 0 0 0 22 12Z" fill="currentColor"/></svg>',
  arrow: '<svg viewBox="0 0 24 24"><path d="M5 12h13m-6-6 6 6-6 6" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  down: '<svg viewBox="0 0 24 24"><path d="M12 4v15m-6-6 6 6 6-6" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  link: '<svg viewBox="0 0 24 24"><path d="M10 14a4.5 4.5 0 0 0 6.36 0l3.18-3.18a4.5 4.5 0 0 0-6.36-6.36L11.6 6.04M14 10a4.5 4.5 0 0 0-6.36 0l-3.18 3.18a4.5 4.5 0 0 0 6.36 6.36l1.58-1.58" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>',
  user: '<svg viewBox="0 0 24 24"><path d="M12 12a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9Zm0 2c-4.4 0-8 2.2-8 5v2h16v-2c0-2.8-3.6-5-8-5Z" fill="currentColor"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  cursor: '<svg viewBox="0 0 24 24"><path d="M4 2.5 19.5 12l-6.8 1.4 3.9 7.3-2.9 1.5-3.8-7.3L4.5 19.6Z" fill="#fff" stroke="#111" stroke-width="1.3" stroke-linejoin="round"/></svg>',
};
function icon(name, cls, parent) { const s = el('span', 'ico ' + (cls || ''), parent); s.innerHTML = ICONS[name] || ''; return s; }
/** Out-animation: no backwards fill, so it doesn't override the in-animation before it starts. */
function AO(node, kf, t, d, ease = 'in', opt = {}) { return A(node, kf, t, d, ease, Object.assign({fill: 'forwards'}, opt)); }
/** Per-frame callbacks (seconds) for things WAAPI can't animate: counters, canvas, text swaps. */
const TICKS = [];
function tick(fn) { TICKS.push(fn); }
window.__onseek = (t) => { for (const f of TICKS) { try { f(t); } catch (e) { console.error(e); } } };
const clamp01 = (x) => Math.max(0, Math.min(1, x));
const ease = {
  outExpo: (x) => (x >= 1 ? 1 : 1 - Math.pow(2, -10 * x)),
  outCubic: (x) => 1 - Math.pow(1 - x, 3),
  inOutCubic: (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2),
  outBack: (x) => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); },
};
/** progress of t inside [a, a+d] (0..1) */
const prog = (t, a, d) => clamp01((t - a) / Math.max(1e-6, d));
/** Brand logo bug (small logo in a corner) when the page was given one: P.logo_uri. */
function logoBug(where = 'tr', sizePx = 90) {
  if (!P.logo_uri || !P.logo_bug) return null;
  const img = el('img', 'logobug');
  img.src = P.logo_uri;
  const m = 56 * U;
  Object.assign(img.style, {position: 'absolute', height: sizePx * U + 'px', width: 'auto', zIndex: 50,
    [where.includes('t') ? 'top' : 'bottom']: m + 'px', [where.includes('l') ? 'left' : 'right']: m + 'px'});
  return img;
}
/** Hex → rgba() with alpha. */
function rgba(hex, a) {
  const h = String(hex).replace('#', '');
  const n = h.length === 3 ? h.split('').map(c => c + c).join('') : h.slice(0, 6);
  const v = parseInt(n, 16);
  if (Number.isNaN(v)) return hex;
  return `rgba(${(v >> 16) & 255},${(v >> 8) & 255},${v & 255},${a})`;
}
/** Split text into beats: new lines or '|' */
function beats(text) { return String(text || '').split(/\n|\|/).map(s => s.trim()).filter(Boolean); }
