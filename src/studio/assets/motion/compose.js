/* studio motion_compose runtime — an After-Effects-style composition from a JSON spec, built on GSAP.
 *
 * window.SPEC (normalised by Python) → DOM layers + ONE paused GSAP master timeline. The renderer's
 * virtual clock calls window.__onseek(t) every frame and we set master.time(t), so every frame is exact
 * and can be rendered in any order (parallel workers, motion-blur sub-frames).
 *
 * Layer DOM:  .L (position, anchor, keyframes)  >  .F (loops: wiggle/float/parallax)  >  .A (presets)  >  .C (content)
 * Arabic / Hebrew text is never split into letters (joining would break): char-level presets fall back to words.
 */
window.__compose = async function () {
  const SPEC = window.SPEC;
  const W = SPEC.width, H = SPEC.height, U = Math.min(W, H) / 1080;
  const C = Object.assign({primary: '#FF5A36', accent: '#FFC53D', background: '#0E0F1A', text: '#FFFFFF',
                           muted: 'rgba(255,255,255,.7)', panel: '#1A1C2E', on_primary: '#FFFFFF', black: '#000000', white: '#FFFFFF'},
                          SPEC.colors || {});
  document.documentElement.style.setProperty('--u', U + 'px');
  for (const [k, v] of Object.entries(C)) document.documentElement.style.setProperty('--' + k, v);
  const RTL_RE = /[֐-ࣿיִ-﷿ﹰ-﻿]/;
  const isRTL = (s) => RTL_RE.test(String(s || ''));
  const comp = document.getElementById('comp'), world = document.getElementById('world');
  if (!SPEC.transparent) { comp.style.background = color(SPEC.background_color || 'background'); document.body.style.background = color(SPEC.fade_color, '#000'); }
  const master = gsap.timeline({paused: true, defaults: {ease: 'expo.out', duration: 0.8}});
  window.__master = master;
  const ticks = [], waits = [];
  const warn = (m) => console.warn('compose: ' + m);
  let seed = (SPEC.seed || 7) >>> 0;
  const rnd = () => { seed = (seed + 0x6D2B79F5) >>> 0; let t = seed; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };

  // ------------------------------------------------------------------ values
  function color(v, d) {
    if (v == null || v === '') return d;
    if (typeof v === 'object') return gradient(v);
    if (typeof v === 'string' && C[v] != null) return C[v];
    return v;
  }
  function gradient(g) {
    const cols = (g.colors || g.linear || g.radial || g.conic || [C.primary, C.accent]).map(c => color(c));
    const kind = g.type || (g.radial ? 'radial' : g.conic ? 'conic' : 'linear');
    if (kind === 'radial') return `radial-gradient(${g.shape || 'circle'} at ${g.at || '50% 50%'}, ${cols.join(', ')})`;
    if (kind === 'conic') return `conic-gradient(from ${g.angle || 0}deg at ${g.at || '50% 50%'}, ${cols.join(', ')})`;
    return `linear-gradient(${g.angle ?? 135}deg, ${cols.join(', ')})`;
  }
  const isGrad = (v) => v && typeof v === 'object';
  /** numbers = canvas px; "50%" of ref; "12u" = 12 × (short side / 1080); "12vw"/"12vh" of the canvas */
  function px(v, ref, d) {
    if (v == null || v === '') return d;
    if (typeof v === 'number') return v;
    const s = String(v).trim();
    let m;
    if ((m = /^(-?[\d.]+)%$/.exec(s))) return parseFloat(m[1]) / 100 * ref;
    if ((m = /^(-?[\d.]+)u$/.exec(s))) return parseFloat(m[1]) * U;
    if ((m = /^(-?[\d.]+)vw$/.exec(s))) return parseFloat(m[1]) / 100 * W;
    if ((m = /^(-?[\d.]+)vh$/.exec(s))) return parseFloat(m[1]) / 100 * H;
    const n = parseFloat(s); return isNaN(n) ? d : n;
  }
  const ANCH = {'center': [.5, .5], 'top-left': [0, 0], 'top': [.5, 0], 'top-right': [1, 0], 'left': [0, .5], 'right': [1, .5],
                'bottom-left': [0, 1], 'bottom': [.5, 1], 'bottom-right': [1, 1]};
  function anchor(a) { if (Array.isArray(a)) return a; return ANCH[a || 'center'] || ANCH.center; }

  // ------------------------------------------------------------------ easing
  const EASE_ALIAS = {
    linear: 'none', none: 'none', smooth: 'power2.inOut', snappy: 'expo.out', soft: 'power2.out', overshoot: 'back.out(1.7)',
    spring: 'elastic.out(1, 0.55)', bouncy: 'bounce.out', anticipate: 'back.inOut(1.7)', 'in': 'power3.in', out: 'expo.out',
    'in-out': 'expo.inOut', inout: 'expo.inOut', 'ease-in': 'power2.in', 'ease-out': 'power2.out', 'ease-in-out': 'power2.inOut',
    cinematic: 'power4.inOut', whip: 'expo.inOut',
  };
  const easeCache = {};
  function ez(n, d = 'expo.out') {
    if (!n) return d;
    if (typeof n !== 'string') return d;
    if (EASE_ALIAS[n]) return EASE_ALIAS[n];
    let m = /^cubic-bezier\(([^)]+)\)$/.exec(n.trim()) || /^\[?\s*([\d.\-]+\s*,\s*[\d.\-]+\s*,\s*[\d.\-]+\s*,\s*[\d.\-]+)\s*\]?$/.exec(n.trim());
    if (m && window.CustomEase) {
      const key = 'cb' + m[1].replace(/[^\d]/g, '_');
      if (!easeCache[key]) easeCache[key] = CustomEase.create(key, m[1].replace(/\s/g, ''));
      return easeCache[key];
    }
    m = /^ease(In|Out|InOut)(Sine|Quad|Cubic|Quart|Quint|Expo|Circ|Back|Elastic|Bounce)$/.exec(n);
    if (m) {
      const map = {Quad: 'power1', Cubic: 'power2', Quart: 'power3', Quint: 'power4'};
      return (map[m[2]] || m[2].toLowerCase()) + '.' + m[1][0].toLowerCase() + m[1].slice(1);
    }
    return n;  // gsap names: power1-4.in/out/inOut, expo.*, back.out(2), elastic.out(1,0.3), bounce.out, steps(5), sine…
  }

  // ------------------------------------------------------------------ time expressions
  /** number = seconds after the layer's in point; "out-0.5" relative to its out point; "in+0.2";
   *  "<marker>" / "<marker>+0.3" = comp marker (absolute); "end-1" = comp end. Returns ABSOLUTE seconds. */
  function T(expr, ctx, d = 0) {
    if (expr == null || expr === '') return ctx.inAbs + d;
    if (typeof expr === 'number') return ctx.inAbs + expr;
    const s = String(expr).trim();
    const m = /^([A-Za-z_][\w-]*)\s*([+-]\s*[\d.]+)?$/.exec(s);
    if (m) {
      const off = m[2] ? parseFloat(m[2].replace(/\s/g, '')) : 0;
      const k = m[1];
      if (k === 'in') return ctx.inAbs + off;
      if (k === 'out') return ctx.outAbs + off;
      if (k === 'end') return TOTAL + off;
      if (k === 'scene') return ctx.sceneStart + off;
      if (SPEC.markers && SPEC.markers[k] != null) return SPEC.markers[k] + off;
      warn('unknown time ' + s);
    }
    const n = parseFloat(s); return isNaN(n) ? ctx.inAbs + d : ctx.inAbs + n;
  }

  // ------------------------------------------------------------------ text helpers
  function esc(s) { return String(s).replace(/[&<>]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'}[c])); }
  /** *word* → emphasis span ; **word** → strong */
  function richText(s) {
    return esc(s).replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>').replace(/\*([^*]+)\*/g, '<span class="emph">$1</span>');
  }
  /** Split .C text into words (always safe for Arabic). Returns word spans; mask wraps each in an overflow box. */
  function splitWords(node, mask) {
    const out = [];
    const walk = (n) => {
      for (const ch of [...n.childNodes]) {
        if (ch.nodeType === 3) {
          const parts = ch.textContent.split(/(\s+)/);
          const frag = document.createDocumentFragment();
          for (const p of parts) {
            if (!p) continue;
            if (/^\s+$/.test(p)) { frag.appendChild(document.createTextNode(p)); continue; }
            const w = document.createElement('span'); w.className = 'sw'; w.textContent = p;
            if (mask) { const m = document.createElement('span'); m.className = 'swm'; m.appendChild(w); frag.appendChild(m); }
            else frag.appendChild(w);
            out.push(w);
          }
          n.replaceChild(frag, ch);
        } else if (ch.nodeType === 1 && !ch.classList.contains('hl') && !ch.classList.contains('uline') && !ch.classList.contains('caret')) walk(ch);
      }
    };
    walk(node);
    return out;
  }
  function splitChars(node, mask) {
    const words = splitWords(node, false);
    const out = [];
    for (const w of words) {
      const txt = w.textContent; w.textContent = '';
      w.style.whiteSpace = 'nowrap';
      for (const ch of txt) {
        const c = document.createElement('span'); c.className = 'sc'; c.textContent = ch;
        if (mask) { const m = document.createElement('span'); m.className = 'swm'; m.style.padding = '.1em 0 .2em'; m.style.margin = '-.1em 0 -.2em'; m.appendChild(c); w.appendChild(m); }
        else w.appendChild(c);
        out.push(c);
      }
    }
    return out;
  }
  /** group word spans by rendered line (offsetTop) → [[w…], …] */
  function lines(words) {
    const rows = [];
    let last = null;
    for (const w of words) {
      const top = Math.round((w.parentElement.classList.contains('swm') ? w.parentElement : w).getBoundingClientRect().top);
      if (last == null || Math.abs(top - last) > 4) { rows.push([]); last = top; }
      rows[rows.length - 1].push(w);
    }
    return rows;
  }
  function fit(node, maxW, maxH, minPx = 10) {
    // centred/right-aligned overflow spills left, which scrollWidth ignores → measure left-aligned
    const ta = node.style.textAlign; node.style.textAlign = 'left';
    let fs = parseFloat(getComputedStyle(node).fontSize);
    for (let i = 0; i < 80 && fs > minPx && (node.scrollWidth > maxW + 1 || node.scrollHeight > maxH + 1); i++) { fs *= 0.96; node.style.fontSize = fs + 'px'; }
    node.style.textAlign = ta;
    return fs;
  }

  function digitsOf(s, mode) { return mode === 'arabic' ? String(s).replace(/[0-9]/g, x => '٠١٢٣٤٥٦٧٨٩'[+x]).replace(/,/g, '٬').replace(/\./g, '٫') : String(s); }
  function counterText(o, v, a = {}) {
    const dec = a.decimals ?? o.decimals ?? 0;
    const n = digitsOf(Number(v).toLocaleString('en-US', {minimumFractionDigits: dec, maximumFractionDigits: dec, useGrouping: (a.separator ?? o.separator) !== false}), a.digits || o.digits);
    const s = (a.prefix ?? o.prefix ?? '') + n + (a.suffix ?? o.suffix ?? '');
    const tpl = o.text || '{n}';
    return tpl.includes('{n}') ? tpl.replace('{n}', s) : s;
  }

  // ------------------------------------------------------------------ shapes → SVG paths
  function shapePath(o, w, h) {
    const k = o.shape || 'rect';
    if (k === 'rect') { const r = Math.min(px(o.radius, Math.min(w, h), 0), w / 2, h / 2);
      return `M${r},0 H${w - r} A${r},${r} 0 0 1 ${w},${r} V${h - r} A${r},${r} 0 0 1 ${w - r},${h} H${r} A${r},${r} 0 0 1 0,${h - r} V${r} A${r},${r} 0 0 1 ${r},0 Z`; }
    if (k === 'circle' || k === 'ellipse' || k === 'ring') { const rx = w / 2, ry = h / 2;
      return `M0,${ry} A${rx},${ry} 0 1 0 ${w},${ry} A${rx},${ry} 0 1 0 0,${ry} Z`; }
    if (k === 'line') return `M0,${h / 2} H${w}`;
    if (k === 'star' || k === 'polygon') {
      const n = o.points_count || (k === 'star' ? 5 : 6), inner = k === 'star' ? (o.inner || 0.45) : 1;
      const pts = [];
      for (let i = 0; i < n * (k === 'star' ? 2 : 1); i++) {
        const a = -Math.PI / 2 + i * Math.PI / (k === 'star' ? n : n / 2);
        const r = (k === 'star' && i % 2) ? inner : 1;
        pts.push([w / 2 + Math.cos(a) * w / 2 * r, h / 2 + Math.sin(a) * h / 2 * r]);
      }
      return 'M' + pts.map(p => p.map(v => v.toFixed(2)).join(',')).join(' L') + ' Z';
    }
    if (k === 'heart') return `M${w / 2},${h * 0.92} C${w * 0.05},${h * 0.6} ${-w * 0.02},${h * 0.18} ${w * 0.26},${h * 0.1} C${w * 0.4},${h * 0.06} ${w * 0.48},${h * 0.18} ${w / 2},${h * 0.26} C${w * 0.52},${h * 0.18} ${w * 0.6},${h * 0.06} ${w * 0.74},${h * 0.1} C${w * 1.02},${h * 0.18} ${w * 0.95},${h * 0.6} ${w / 2},${h * 0.92} Z`;
    if (k === 'triangle') return `M${w / 2},0 L${w},${h} L0,${h} Z`;
    if (k === 'arrow') return `M0,${h * 0.35} H${w * 0.6} V0 L${w},${h / 2} L${w * 0.6},${h} V${h * 0.65} H0 Z`;
    if (k === 'path') return o.d || '';
    return '';
  }
  let gradId = 0;
  function svgPaint(svg, v, fallback) {
    if (!isGrad(v)) return color(v, fallback);
    const id = 'g' + (++gradId);
    const cols = (v.colors || v.linear || v.radial || [C.primary, C.accent]).map(c => color(c));
    const radial = v.type === 'radial' || v.radial;
    const a = ((v.angle ?? 135) - 90) * Math.PI / 180;
    const g = document.createElementNS('http://www.w3.org/2000/svg', radial ? 'radialGradient' : 'linearGradient');
    g.id = id;
    if (!radial) { g.setAttribute('x1', 50 - Math.cos(a) * 50 + '%'); g.setAttribute('y1', 50 - Math.sin(a) * 50 + '%');
                   g.setAttribute('x2', 50 + Math.cos(a) * 50 + '%'); g.setAttribute('y2', 50 + Math.sin(a) * 50 + '%'); }
    cols.forEach((c, i) => { const s = document.createElementNS('http://www.w3.org/2000/svg', 'stop'); s.setAttribute('offset', (i / Math.max(1, cols.length - 1) * 100) + '%'); s.setAttribute('stop-color', c); g.appendChild(s); });
    let defs = svg.querySelector('defs'); if (!defs) { defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs'); svg.prepend(defs); }
    defs.appendChild(g);
    return `url(#${id})`;
  }

  const ICONS = Object.assign({
    check: '<path d="m5 12.5 4.5 4.5L19 7.5" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>',
    arrow: '<path d="M5 12h13m-6-6 6 6-6 6" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>',
    star: '<path d="m12 2.8 2.8 5.9 6.4.8-4.7 4.4 1.2 6.4L12 17.2l-5.7 3.1 1.2-6.4-4.7-4.4 6.4-.8Z" fill="currentColor"/>',
    heart: '<path d="M12 20.5s-7.5-4.6-9.2-9.4C1.6 7.6 3.9 4.5 7.2 4.5c2 0 3.6 1.1 4.8 2.8 1.2-1.7 2.8-2.8 4.8-2.8 3.3 0 5.6 3.1 4.4 6.6-1.7 4.8-9.2 9.4-9.2 9.4Z" fill="currentColor"/>',
    play: '<path d="M8 5.5v13l10.5-6.5Z" fill="currentColor"/>',
    bolt: '<path d="M13 2 4.5 13.5H11L10 22l8.5-11.5H12Z" fill="currentColor"/>',
    rocket: '<path d="M14 3.5c3.4-.9 6-.7 6.5-.2s.7 3.1-.2 6.5c-.8 3-3.4 6-6.3 7.9l-.5 3.3-2.6-1.7-4.2-4.2-1.7-2.6 3.3-.5C10 9.2 11 4.3 14 3.5Zm1.5 3.5a1.8 1.8 0 1 0 2.6 2.5A1.8 1.8 0 0 0 15.5 7ZM6 15.5l2.5 2.5c-1 1.6-3 2.4-5 2.5.1-2 .9-4 2.5-5Z" fill="currentColor"/>',
    globe: '<g fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.6 2.6 3.8 5.6 3.8 9s-1.2 6.4-3.8 9c-2.6-2.6-3.8-5.6-3.8-9S9.4 5.6 12 3Z"/></g>',
    pin: '<path d="M12 22s7-6.6 7-12.2A7 7 0 0 0 5 9.8C5 15.4 12 22 12 22Zm0-9.3a2.8 2.8 0 1 1 0-5.6 2.8 2.8 0 0 1 0 5.6Z" fill="currentColor"/>',
    cart: '<path d="M3 4h2.2l2.3 10.6a1.6 1.6 0 0 0 1.6 1.3h8.4a1.6 1.6 0 0 0 1.6-1.2L21 8H6.3M9.5 20.5a1.2 1.2 0 1 1 0-2.4 1.2 1.2 0 0 1 0 2.4Zm8 0a1.2 1.2 0 1 1 0-2.4 1.2 1.2 0 0 1 0 2.4Z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
    clock: '<g fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/></g>',
    calendar: '<g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/></g>',
    chart: '<path d="M4 20V10m5.3 10V4m5.4 16v-7M20 20V8" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/>',
    mail: '<g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><rect x="3" y="5.5" width="18" height="13" rx="2.5"/><path d="m4 7 8 6 8-6"/></g>',
    phone: '<path d="M6.6 3.5h2.6l1.4 4.3-2 1.5a12 12 0 0 0 6.1 6.1l1.5-2 4.3 1.4v2.6a2 2 0 0 1-2 2A16.5 16.5 0 0 1 4.6 5.5a2 2 0 0 1 2-2Z" fill="currentColor"/>',
    user: '<path d="M12 12a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9Zm0 2c-4.4 0-8 2.2-8 5v2h16v-2c0-2.8-3.6-5-8-5Z" fill="currentColor"/>',
    bell: '<path d="M12 22a2.5 2.5 0 0 0 2.45-2h-4.9A2.5 2.5 0 0 0 12 22Zm7-6V11a7 7 0 0 0-5.5-6.84V3.5a1.5 1.5 0 0 0-3 0v.66A7 7 0 0 0 5 11v5l-2 2v1h18v-1Z" fill="currentColor"/>',
    plus: '<path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/>',
    quote: '<path d="M4 18v-5.5C4 8.4 6.2 6 10 5.5v2.6c-2 .5-3 1.8-3 3.9h3V18Zm10 0v-5.5c0-4.1 2.2-6.5 6-7v2.6c-2 .5-3 1.8-3 3.9h3V18Z" fill="currentColor"/>',
    spark: '<path d="M12 2.5c.7 4.8 3.7 7.8 8.5 8.5v1c-4.8.7-7.8 3.7-8.5 8.5h-1c-.7-4.8-3.7-7.8-8.5-8.5v-1c4.8-.7 7.8-3.7 8.5-8.5Z" fill="currentColor"/>',
    download: '<path d="M12 4v11m-5-5 5 5 5-5M5 20h14" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>',
  }, SPEC.icons || {});

  // ------------------------------------------------------------------ build a layer
  function build(o, parent, sceneCtx, parentIn, parentOut) {
    const type = o.type || (o.text != null ? 'text' : o.children ? 'group' : 'shape');
    const ctx = {o, type, sceneStart: sceneCtx.start};
    ctx.inAbs = parentIn + (typeof o.in === 'number' ? o.in : 0);
    if (typeof o.in === 'string') ctx.inAbs = T(o.in, {inAbs: parentIn, outAbs: parentOut, sceneStart: sceneCtx.start});
    ctx.outAbs = o.out == null ? parentOut : (typeof o.out === 'number' ? parentIn + o.out : T(o.out, {inAbs: parentIn, outAbs: parentOut, sceneStart: sceneCtx.start}));
    if (o.duration != null && o.out == null) ctx.outAbs = ctx.inAbs + o.duration;
    ctx.outAbs = Math.min(ctx.outAbs, parentOut);
    const L = document.createElement('div'); L.className = 'L ' + type + (o.class ? ' ' + o.class : '');
    if (o.id) L.id = o.id;
    const F = document.createElement('div'); F.className = 'F';
    const A = document.createElement('div'); A.className = 'A';
    const Cn = document.createElement('div'); Cn.className = 'C';
    L.appendChild(F); F.appendChild(A); A.appendChild(Cn); parent.appendChild(L);
    Object.assign(ctx, {L, F, A, C: Cn});
    let w = px(o.w, W, null), h = px(o.h, H, null);
    const st = L.style;
    if (o.z_index != null) st.zIndex = o.z_index;
    if (o.blend) st.mixBlendMode = o.blend;
    if (o.opacity != null) A.style.opacity = o.opacity;
    if (o.style) Object.assign(Cn.style, o.style);

    if (type === 'text' || type === 'counter') {
      const text = type === 'counter' ? (o.text || '{n}') : String(o.text ?? '');
      const rtl = o.rtl ?? isRTL(text);
      ctx.rtl = rtl; ctx.isText = true; ctx.plain = text.replace(/\*/g, '');
      Cn.dir = rtl ? 'rtl' : 'ltr';
      const role = o.font || 'head';
      const fam = role === 'head' ? 'var(--font-head)' : role === 'body' ? 'var(--font-body)' : `'${(SPEC.fontmap || {})[role] || role}'`;
      Object.assign(Cn.style, {
        fontFamily: `${fam}, var(--font-body), sans-serif`,
        fontWeight: o.weight || (role === 'body' ? 500 : 800), fontSize: px(o.size, H, 80 * U) + 'px',
        lineHeight: o.line_height || (rtl ? 1.45 : 1.08),
        letterSpacing: o.letter_spacing != null ? (typeof o.letter_spacing === 'number' ? o.letter_spacing + 'em' : o.letter_spacing) : (rtl ? '0' : (role === 'body' ? '0' : '-0.02em')),
        textAlign: o.align || (rtl ? 'right' : 'left'), textTransform: o.uppercase ? 'uppercase' : '', fontStyle: o.italic ? 'italic' : '',
      });
      if (isGrad(o.color)) Object.assign(Cn.style, {backgroundImage: gradient(o.color), webkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent'});
      else Cn.style.color = color(o.color, C.text);
      if (o.stroke) { Cn.style.webkitTextStroke = `${px(o.stroke_width, H, 2 * U)}px ${color(o.stroke)}`; if (o.fill === false) Cn.style.color = 'transparent'; }
      const sh = [];
      if (o.shadow) sh.push(typeof o.shadow === 'string' ? o.shadow : `0 ${4 * U}px ${24 * U}px rgba(0,0,0,.45)`);
      if (o.glow) { const gc = color(o.glow === true ? 'primary' : o.glow), gs = o.glow_strength ?? 0.6;
        sh.push(`0 0 ${14 * U}px ${rgba(gc, 0.8 * gs)}`, `0 0 ${46 * U}px ${rgba(gc, 0.55 * gs)}`); }
      if (sh.length) Cn.style.textShadow = sh.join(', ');
      if (o.bg) {
        const b = typeof o.bg === 'object' && !Array.isArray(o.bg) && !o.bg.colors ? o.bg : {color: o.bg};
        const pad = b.pad || [0.45, 0.22];
        Object.assign(Cn.style, {background: color(b.color, C.primary), padding: `${pad[1]}em ${pad[0]}em`, borderRadius: px(b.radius, H, 0.18 * px(o.size, H, 80 * U)) + 'px', width: 'auto'});
        L.style.width = 'max-content';
      }
      Cn.innerHTML = type === 'counter' ? esc(counterText(o, o.value ?? 0)) : richText(text);
      Cn.querySelectorAll('.emph').forEach(e => {
        const ec = color(o.emph_color, C.primary);
        if ((o.emph_style || 'color') === 'color') e.style.color = ec;
        else if (o.emph_style === 'highlight') { const hl = document.createElement('span'); hl.className = 'hl'; hl.style.background = ec; e.prepend(hl); e.style.color = color(o.emph_text, C.background); }
        else if (o.emph_style === 'outline') { e.style.color = 'transparent'; e.style.webkitTextStroke = `${0.025}em ${ec}`; }
        else if (o.emph_style === 'underline') { const u = document.createElement('span'); u.className = 'uline'; u.style.background = ec; e.appendChild(u); }
      });
      if (w) { st.width = w + 'px'; } else { st.width = 'max-content'; st.maxWidth = px(o.max_w, W, W * 0.88) + 'px'; }
      if (o.nowrap) L.classList.add('nowrap');
      if (h) st.height = h + 'px';
    } else if (type === 'shape') {
      w = w ?? px(o.size, H, 200 * U); h = h ?? (o.shape === 'line' ? px(o.stroke_width, H, 6 * U) : w);
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('viewBox', o.viewBox || `0 0 ${w} ${h}`); svg.setAttribute('preserveAspectRatio', o.viewBox ? 'xMidYMid meet' : 'none');
      const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      p.setAttribute('d', shapePath(o, w, h));
      const ring = o.shape === 'ring' || o.shape === 'line';
      p.setAttribute('fill', ring || o.fill === 'none' ? 'none' : svgPaint(svg, o.fill ?? o.color, C.primary));
      if (o.stroke || ring) { p.setAttribute('stroke', svgPaint(svg, o.stroke || o.color || o.fill, C.primary)); p.setAttribute('stroke-width', px(o.stroke_width, H, 6 * U));
        p.setAttribute('stroke-linecap', o.cap || 'round'); p.setAttribute('stroke-linejoin', 'round'); p.setAttribute('vector-effect', 'non-scaling-stroke'); }
      if (o.dash) p.setAttribute('stroke-dasharray', o.dash);
      svg.appendChild(p); Cn.appendChild(svg);
      if (o.shadow) Cn.style.filter = `drop-shadow(0 ${10 * U}px ${30 * U}px rgba(0,0,0,.35))`;
      if (o.blur) Cn.style.filter = `blur(${px(o.blur, H, 0)}px)`;
    } else if (type === 'image' || type === 'video') {
      const m = document.createElement(type === 'image' ? 'img' : 'video');
      m.src = o.src;
      if (type === 'video') { m.muted = true; m.playsInline = true; m.preload = 'auto'; m.dataset.start = String(ctx.inAbs - (o.trim || 0)); m.dataset.rate = String(o.speed || 1);
        if (o.loop) m.dataset.loop = 'true';
        waits.push(new Promise(r => { m.addEventListener('loadeddata', r, {once: true}); m.addEventListener('error', () => { warn('video failed ' + o.src); r(); }, {once: true}); setTimeout(r, 15000); })); }
      else waits.push(m.decode ? m.decode().catch(() => warn('image failed ' + o.src)) : Promise.resolve());
      m.style.objectFit = o.fit || 'cover'; m.style.objectPosition = o.position || '50% 50%';
      Cn.appendChild(m);
      if (o.radius != null) { Cn.style.borderRadius = px(o.radius, Math.min(w || W, h || H), 0) + 'px'; Cn.style.overflow = 'hidden'; }
      if (o.border) Cn.style.boxShadow = `inset 0 0 0 ${px(o.border_width, H, 4 * U)}px ${color(o.border)}`;
      if (o.shadow) L.style.filter = `drop-shadow(0 ${18 * U}px ${44 * U}px rgba(0,0,0,.45))`;
      if (!w && !h) { w = W; h = H; }
      else if (!h && o.aspect) h = w / o.aspect; else if (!w && o.aspect) w = h * o.aspect;
      else if (!h || !w) { const r = o.natural_aspect || 16 / 9; if (!h) h = w / r; else w = h * r; }
    } else if (type === 'svg') {
      Cn.innerHTML = o.svg || '';
      const svg = Cn.querySelector('svg');
      if (svg) { svg.removeAttribute('width'); svg.removeAttribute('height'); svg.setAttribute('preserveAspectRatio', o.fit === 'stretch' ? 'none' : 'xMidYMid meet'); }
      if (o.color) Cn.querySelectorAll('path,circle,rect,ellipse,polygon,polyline,text').forEach(e => { if (e.getAttribute('fill') !== 'none') e.setAttribute('fill', color(o.color)); });
      const r = o.natural_aspect || 1;
      if (!w && !h) { w = 400 * U * Math.sqrt(r); h = w / r; } else if (!h) h = w / r; else if (!w) w = h * r;
      if (o.shadow) L.style.filter = `drop-shadow(0 ${10 * U}px ${30 * U}px rgba(0,0,0,.35))`;
    } else if (type === 'icon') {
      const s = px(o.size, H, 120 * U); w = w ?? s; h = h ?? s;
      Cn.innerHTML = `<svg viewBox="0 0 24 24">${ICONS[o.name] || ICONS.spark}</svg>`;
      Cn.style.color = color(o.color, C.primary);
      if (o.bg) { Object.assign(Cn.style, {background: color(o.bg), borderRadius: o.round === false ? '18%' : '50%', padding: (o.pad ?? 0.22) * s + 'px'}); }
    } else if (type === 'lottie') {
      if (!window.lottie) warn('lottie-web not loaded');
      w = w ?? px(o.size, H, 500 * U); h = h ?? w / (o.natural_aspect || 1);
      if (window.lottie && o.data) {
        const an = lottie.loadAnimation({container: Cn, renderer: 'svg', loop: false, autoplay: false, animationData: o.data,
                                         rendererSettings: {preserveAspectRatio: o.fit === 'cover' ? 'xMidYMid slice' : 'xMidYMid meet'}});
        an.__studio = {start: ctx.inAbs, speed: o.speed || 1, loop: o.loop ?? true};
        waits.push(new Promise(r => { if (an.isLoaded) r(); an.addEventListener('DOMLoaded', r); setTimeout(r, 5000); }));
      }
    } else if (type === 'chart') {
      w = w ?? W * 0.7; h = h ?? H * 0.6;
      chart(ctx, o, w, h);
    } else if (type === '3d') {
      w = w ?? W; h = h ?? H;
      const cv = document.createElement('canvas'); cv.width = Math.round(w); cv.height = Math.round(h); Cn.appendChild(cv);
      if (!window.__studio3d) warn('3D module not loaded');
      else waits.push(window.__studio3d.createScene(cv, Object.assign({duration: ctx.outAbs - ctx.inAbs, transparent: true}, o.scene || {})).then(sc => {
        ticks.push((t) => { if (t >= ctx.inAbs - 0.001 && t <= ctx.outAbs + 0.001) sc.render(t - ctx.inAbs); });
      }).catch(e => warn('3d: ' + e)));
    } else if (type === 'group') {
      if (w) st.width = w + 'px'; if (h) st.height = h + 'px';
      for (const ch of (o.children || [])) build(ch, Cn, sceneCtx, ctx.inAbs, ctx.outAbs);
    }
    if (w != null && type !== 'text') st.width = w + 'px';
    if (h != null && type !== 'text') st.height = h + 'px';
    if (type !== 'group' && type !== 'text' && w == null) st.width = 'max-content';

    // ---- position: x/y of the anchor point (default centre of the canvas)
    if (ctx.isText && o.fit !== false && (w || o.fit)) fit(Cn, w || W * 0.88, h || H * 0.9);
    const [ax, ay] = anchor(o.anchor || (ctx.isText && !o.anchor ? (o.align === 'left' || (o.align === 'start' && !ctx.rtl)) ? 'left' : (o.align === 'right' || (o.align === 'start' && ctx.rtl)) ? 'right' : 'center' : 'center'));
    const bx = px(o.x, W, W / 2);
    let by = px(o.y, H, H / 2);
    // relative stacking: below/above another layer (by id) with a gap — real measured heights, so wrapped
    // headlines never collide with the line under them (any language, any font)
    const relId = o.below || o.above;
    if (relId && BYID[relId]) {
      const R = BYID[relId], rh = R.L.offsetHeight || 0, mh = L.offsetHeight || 0, gap = px(o.gap, H, 28 * U);
      const rTop = R.by - R.ay * rh;
      by = o.below ? rTop + rh + gap + ay * mh : rTop - gap - (1 - ay) * mh;
    } else if (relId) warn(`layer ${o.id || type}: ${o.below ? 'below' : 'above'} '${relId}' not found (it must come earlier in the scene)`);
    ctx.bx = bx; ctx.by = by; ctx.ay = ay; ctx.L = L;
    if (o.id) BYID[o.id] = ctx;
    gsap.set(L, {x: bx, y: by, xPercent: -ax * 100, yPercent: -ay * 100, transformOrigin: `${ax * 100}% ${ay * 100}%`,
                 scale: o.scale ?? 1, rotation: o.rotation || 0, z: px(o.z, H, 0), skewX: o.skew || 0});
    gsap.set([F, A], {transformOrigin: `${ax * 100}% ${ay * 100}%`});
    // ---- visibility window
    if (ctx.inAbs > 0.0005) { L.style.visibility = 'hidden'; master.set(L, {visibility: 'hidden'}, 0); master.set(L, {visibility: 'inherit'}, ctx.inAbs); }
    if (ctx.outAbs < TOTAL - 0.0005) master.set(L, {visibility: 'hidden'}, ctx.outAbs);
    layers.push(ctx);
    return ctx;
  }

  // ------------------------------------------------------------------ chart (plain SVG, real values to scale)
  function chart(ctx, o, w, h) {
    const kind = o.chart || 'bar';
    const data = (o.data || []).map(d => ({label: String(d.label ?? ''), value: +d.value || 0, color: d.color}));
    const pal = (o.colors || ['primary', 'accent', 'text', 'muted']).map(c => color(c));
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`); svg.classList.add('chart');
    ctx.C.appendChild(svg);
    const NS = 'http://www.w3.org/2000/svg';
    const mk = (tag, attrs, parent = svg) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); parent.appendChild(e); return e; };
    const fs = px(o.font_size, H, Math.max(16 * U, Math.min(w, h) * 0.055));
    const tc = color(o.text_color, C.text), mc = color(o.muted_color, C.muted);
    const dig = (s) => o.digits === 'arabic' ? String(s).replace(/[0-9]/g, d => '٠١٢٣٤٥٦٧٨٩'[+d]).replace(/,/g, '٬').replace(/\./g, '٫') : String(s);
    const fmt = (v) => dig((o.prefix || '') + Number(v).toLocaleString('en-US', {minimumFractionDigits: o.decimals || 0, maximumFractionDigits: o.decimals || 0}) + (o.suffix || ''));
    const at = T(o.at ?? 0.1, ctx), dur = o.grow ?? 1.4, stg = o.stagger ?? 0.12;
    const max = o.max ?? (Math.max(...data.map(d => d.value), 0) || 1);
    const counters = [], anchors = [];
    const rtl = data.some(d => isRTL(d.label));
    if (kind === 'bar' || kind === 'column' || kind === 'hbar') {
      const horiz = kind === 'hbar';
      const n = data.length || 1;
      if (!horiz) {
        const top = fs * 2.2, bottom = fs * 2.4, avail = h - top - bottom;
        const slot = w / n, bw = Math.min(slot * (o.bar_width ?? 0.62), fs * 7);
        if (o.grid !== false) for (let g = 0; g <= 4; g++) { const y = top + avail * g / 4; const ln = mk('line', {x1: 0, x2: w, y1: y, y2: y, stroke: mc, 'stroke-opacity': g === 4 ? 0.5 : 0.14, 'stroke-width': Math.max(1, U * 1.5)});
          master.from(ln, {attr: {x2: 0}, duration: 0.9, ease: 'expo.inOut'}, at - 0.2 + g * 0.03); }
        data.forEach((d, i) => {
          const order = rtl ? n - 1 - i : i;
          const x = slot * order + (slot - bw) / 2, bh = Math.max(0, d.value) / max * avail, y = top + avail - bh;
          const r = mk('rect', {x, y, width: bw, height: Math.max(0.01, bh), rx: Math.min(bw * 0.12, fs * 0.4), fill: color(d.color, o.highlight == null ? pal[0] : i === o.highlight ? C.accent : rgba(C.text, 0.22))});
          anchors[i] = {x: x + bw / 2, y: y - fs * 1.9};
          master.from(r, {attr: {y: top + avail, height: 0.01}, duration: dur, ease: 'expo.out'}, at + i * stg);
          const lab = mk('text', {x: x + bw / 2, y: h - bottom + fs * 1.5, 'text-anchor': 'middle', fill: mc, 'font-size': fs});
          lab.textContent = d.label;
          master.from(lab, {opacity: 0, y: fs * 0.6, duration: 0.6}, at + i * stg + 0.2);
          const val = mk('text', {x: x + bw / 2, y: y - fs * 0.55, 'text-anchor': 'middle', fill: tc, 'font-size': fs * 1.15, class: 'val'});
          counters.push({el: val, to: d.value, at: at + i * stg, d: dur});
          master.from(val, {attr: {y: top + avail - fs * 0.55}, duration: dur, ease: 'expo.out'}, at + i * stg);
          master.from(val, {opacity: 0, duration: 0.3}, at + i * stg);
        });
      } else {
        const lw = o.label_width != null ? px(o.label_width, w, 0) : Math.min(w * 0.3, fs * Math.max(...data.map(d => d.label.length), 3) * 0.6);
        const vw = fs * 4.2, slot = h / n, bh = Math.min(slot * 0.6, fs * 2.4);
        data.forEach((d, i) => {
          const y = slot * i + (slot - bh) / 2, bwid = Math.max(0, d.value) / max * (w - lw - vw);
          const x0 = rtl ? w - lw : lw;
          const r = mk('rect', {x: rtl ? x0 - bwid : x0, y, width: Math.max(0.01, bwid), height: bh, rx: bh * 0.18, fill: color(d.color, o.highlight == null ? pal[0] : i === o.highlight ? C.accent : rgba(C.text, 0.22))});
          anchors[i] = {x: rtl ? x0 - bwid - fs * 3 : x0 + bwid + fs * 3, y: y - fs * 0.3};
          master.from(r, {attr: {width: 0.01, x: x0}, duration: dur, ease: 'expo.out'}, at + i * stg);
          const lab = mk('text', {x: rtl ? w : lw - fs * 0.7, y: y + bh / 2 + fs * 0.35, 'text-anchor': 'end', fill: mc, 'font-size': fs});
          if (rtl) { lab.setAttribute('x', w - fs * 0.2); lab.setAttribute('text-anchor', 'start'); lab.setAttribute('direction', 'rtl'); lab.setAttribute('x', w); }
          lab.textContent = d.label;
          master.from(lab, {opacity: 0, duration: 0.5}, at + i * stg);
          const val = mk('text', {x: rtl ? x0 - bwid - fs * 0.5 : x0 + bwid + fs * 0.5, y: y + bh / 2 + fs * 0.4, 'text-anchor': rtl ? 'end' : 'start', fill: tc, 'font-size': fs * 1.1, class: 'val'});
          counters.push({el: val, to: d.value, at: at + i * stg, d: dur});
          master.from(val, {attr: {x: x0 + (rtl ? -fs * 0.5 : fs * 0.5)}, duration: dur, ease: 'expo.out'}, at + i * stg);
        });
      }
    } else if (kind === 'line' || kind === 'area') {
      const top = fs * 2, bottom = fs * 2.4, left = fs * 0.8, right = fs * 0.8, avail = h - top - bottom;
      const n = data.length, step = (w - left - right) / Math.max(1, n - 1);
      const lo = o.min ?? Math.min(0, ...data.map(d => d.value));
      const pts = data.map((d, i) => [left + (rtl ? n - 1 - i : i) * step, top + avail - (d.value - lo) / (max - lo || 1) * avail]);
      for (let g = 0; g <= 4; g++) { const y = top + avail * g / 4; mk('line', {x1: 0, x2: w, y1: y, y2: y, stroke: mc, 'stroke-opacity': g === 4 ? 0.5 : 0.12, 'stroke-width': Math.max(1, U * 1.5)}); }
      let dpath = '';
      pts.forEach((p, i) => {  // smooth (Catmull-Rom → Bézier)
        if (i === 0) { dpath = `M${p[0]},${p[1]}`; return; }
        const p0 = pts[i - 2] || pts[i - 1], p1 = pts[i - 1], p2 = p, p3 = pts[i + 1] || p;
        const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6], c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
        dpath += ` C${c1[0]},${c1[1]} ${c2[0]},${c2[1]} ${p2[0]},${p2[1]}`;
      });
      if (kind === 'area') {
        const ga = svgPaint(svg, {type: 'linear', angle: 180, colors: [rgba(pal[0], 0.55), rgba(pal[0], 0)]});
        const ar = mk('path', {d: dpath + ` L${pts[n - 1][0]},${top + avail} L${pts[0][0]},${top + avail} Z`, fill: ga});
        master.from(ar, {opacity: 0, duration: 0.8}, at + dur * 0.6);
      }
      const ln = mk('path', {d: dpath, fill: 'none', stroke: pal[0], 'stroke-width': px(o.stroke_width, H, 6 * U), 'stroke-linecap': 'round', 'stroke-linejoin': 'round'});
      master.fromTo(ln, {drawSVG: rtl ? '100% 100%' : '0%'}, {drawSVG: '0% 100%', duration: dur, ease: 'power2.inOut'}, at);
      pts.forEach((p, i) => {
        anchors[i] = {x: p[0], y: p[1] - fs * 2.6};
        const tt = at + dur * (rtl ? (n - 1 - i) : i) / Math.max(1, n - 1);
        const c = mk('circle', {cx: p[0], cy: p[1], r: fs * 0.32, fill: color('background'), stroke: pal[0], 'stroke-width': px(o.stroke_width, H, 5 * U)});
        master.from(c, {attr: {r: 0}, duration: 0.5, ease: 'back.out(3)'}, tt);
        const lab = mk('text', {x: p[0], y: h - bottom + fs * 1.5, 'text-anchor': 'middle', fill: mc, 'font-size': fs}); lab.textContent = data[i].label;
        master.from(lab, {opacity: 0, duration: 0.4}, tt);
        if (o.show_values !== false) { const v = mk('text', {x: p[0], y: p[1] - fs * 0.9, 'text-anchor': 'middle', fill: tc, 'font-size': fs * 1.05, class: 'val'}); v.textContent = fmt(data[i].value);
          master.from(v, {opacity: 0, attr: {y: p[1] - fs * 0.3}, duration: 0.5}, tt + 0.1); }
      });
    } else if (kind === 'pie' || kind === 'donut') {
      const R = Math.min(w * 0.5, h) / 2 * 0.92, cx = o.legend === false ? w / 2 : w * 0.3, cy = h / 2;
      const sw = kind === 'donut' ? R * (o.thickness ?? 0.32) : R;
      const rr = kind === 'donut' ? R - sw / 2 : R / 2;
      const circ = 2 * Math.PI * rr, total = data.reduce((a, d) => a + Math.max(0, d.value), 0) || 1;
      let acc = 0;
      const g = mk('g', {transform: `rotate(-90 ${cx} ${cy})`});
      data.forEach((d, i) => {
        const frac = Math.max(0, d.value) / total, len = frac * circ;
        const cc = color(d.color, pal[i % pal.length] === C.text ? rgba(C.text, 0.8) : pal[i % pal.length]);
        const s = mk('circle', {cx, cy, r: rr, fill: 'none', stroke: cc, 'stroke-width': kind === 'donut' ? sw : rr * 2,
          'stroke-dasharray': `0 ${circ}`, 'stroke-dashoffset': -acc}, g);
        master.to(s, {attr: {'stroke-dasharray': `${Math.max(0, len - (kind === 'donut' ? U * 3 : 0))} ${circ}`}, duration: dur * frac + 0.3, ease: 'power2.inOut'}, at + (acc / circ) * dur);
        acc += len;
        if (o.legend !== false) {
          const ly = cy - (data.length - 1) * fs * 1.1 + i * fs * 2.2;
          const sq = mk('rect', {x: w * 0.62, y: ly - fs * 0.45, width: fs * 0.9, height: fs * 0.9, rx: fs * 0.2, fill: cc});
          const lt = mk('text', {x: w * 0.62 + fs * 1.5, y: ly + fs * 0.38, fill: tc, 'font-size': fs * 1.05}); lt.textContent = `${d.label}  ${dig(Math.round(frac * 100))}%`;
          master.from([sq, lt], {opacity: 0, x: fs, duration: 0.6}, at + 0.2 + i * stg);
        }
      });
      if (kind === 'donut' && o.center !== false) {
        const v = mk('text', {x: cx, y: cy + fs * 0.6, 'text-anchor': 'middle', fill: tc, 'font-size': fs * 2.2, class: 'val'});
        counters.push({el: v, to: o.center_value ?? total, at, d: dur});
        if (o.center_label) { const l2 = mk('text', {x: cx, y: cy + fs * 2.1, 'text-anchor': 'middle', fill: mc, 'font-size': fs * 0.9}); l2.textContent = o.center_label; }
      }
    }
    for (const c of counters) {
      const obj = {v: 0};
      c.el.textContent = fmt(0);
      master.to(obj, {v: c.to, duration: c.d, ease: 'expo.out', onUpdate: () => { c.el.textContent = fmt(obj.v); }}, c.at);
    }
    // annotations: [{index, text}] → callout above the data point
    (o.annotations || []).forEach((an, k) => {
      const i = an.index ?? 0, A0 = anchors[i];
      const tt = T(an.at ?? (o.at ?? 0.1) + dur + 0.2 + k * 0.4, ctx);
      const box = document.createElement('div');
      const left = an.x != null ? an.x + '%' : A0 ? A0.x / w * 100 + '%' : '50%', top = an.y != null ? an.y + '%' : A0 ? A0.y / h * 100 + '%' : '0%';
      Object.assign(box.style, {position: 'absolute', left, top, transform: 'translate(-50%,-100%)', background: color(an.color, C.accent),
        color: color(an.text_color, C.background), font: `700 ${fs * 0.95}px var(--font-head), sans-serif`, padding: `${fs * 0.35}px ${fs * 0.7}px`, borderRadius: fs * 0.4 + 'px', whiteSpace: 'nowrap'});
      box.textContent = an.text; ctx.C.appendChild(box);
      master.from(box, {scale: 0, opacity: 0, duration: 0.6, ease: 'back.out(2.2)'}, tt);
    });
  }
  function rgba(c, a) {
    const m = /^#?([\da-f]{6})$/i.exec(String(c).trim());
    if (!m) return c; const v = parseInt(m[1], 16); return `rgba(${(v >> 16) & 255},${(v >> 8) & 255},${v & 255},${a})`;
  }

  // ------------------------------------------------------------------ animation presets
  const D = (o, d) => (o.duration ?? d);
  function sideVars(from, dist) {
    switch (from) { case 'right': return {x: dist}; case 'top': return {y: -dist}; case 'bottom': return {y: dist}; default: return {x: -dist}; }
  }
  const CLIP = {left: 'inset(-30% 100% -30% -30%)', right: 'inset(-30% -30% -30% 100%)', top: 'inset(-30% -30% 100% -30%)', bottom: 'inset(100% -30% -30% -30%)', full: 'inset(-30% -30% -30% -30%)'};
  /** Split once and reuse: later presets get the same word/char spans (re-splitting would orphan the
   *  tweens and decorations made by earlier presets). Chars are split inside the existing words. */
  function textTargets(ctx, unit, mask) {
    if (!ctx.isText) return [ctx.A];
    if (!ctx._words) ctx._words = splitWords(ctx.C, mask);
    if (unit !== 'chars' || ctx.rtl) return ctx._words;
    if (!ctx._chars) {
      ctx._chars = [];
      for (const w of ctx._words) {
        const txt = w.textContent; w.textContent = ''; w.style.whiteSpace = 'nowrap';
        for (const ch of txt) {
          const c = document.createElement('span'); c.className = 'sc'; c.textContent = ch;
          if (mask) { const m = document.createElement('span'); m.className = 'swm'; m.style.padding = '.1em 0 .2em'; m.style.margin = '-.1em 0 -.2em'; m.appendChild(c); w.appendChild(m); }
          else w.appendChild(c);
          ctx._chars.push(c);
        }
      }
    }
    return ctx._chars;
  }
  function staggerFor(ctx, targets, o, unit) {
    const each = o.stagger ?? (unit === 'chars' ? 0.03 : unit === 'lines' ? 0.12 : 0.07);
    if (unit === 'lines') {  // same delay for every word on a line
      const rows = lines(targets);
      const idx = new Map(); rows.forEach((r, i) => r.forEach(w => idx.set(w, i)));
      return (i, el) => idx.get(el) * each;
    }
    return {each, from: o.stagger_from || (ctx.rtl ? 'start' : 'start')};
  }
  const PRESETS = {
    fade: (c, o, at, d, e, out) => tw(c.A, {opacity: 0}, at, d, e, out),
    slide: (c, o, at, d, e, out) => { const dist = o.distance === 'offscreen' ? (['top', 'bottom'].includes(o.from) ? H : W) : px(o.distance, H, 160 * U);
      return tw(c.A, Object.assign({opacity: o.fade === false ? 1 : 0}, sideVars(o.from || o.direction || (c.rtl ? 'right' : 'left'), dist)), at, d, e, out); },
    rise: (c, o, at, d, e, out) => tw(c.A, {y: px(o.distance, H, 60 * U), opacity: 0}, at, d, e, out),
    drop: (c, o, at, d, e, out) => tw(c.A, {y: -px(o.distance, H, 60 * U), opacity: 0}, at, d, e, out),
    scale: (c, o, at, d, e, out) => tw(c.A, {scale: o.from_scale ?? 0.6, opacity: 0}, at, d, e, out),
    zoom: (c, o, at, d, e, out) => tw(c.A, {scale: o.from_scale ?? 1.6, opacity: 0, filter: `blur(${10 * U}px)`}, at, d, e || 'expo.out', out),
    pop: (c, o, at, d, e, out) => tw(c.A, {scale: 0, opacity: 0}, at, d, out ? (e || 'back.in(2)') : (e || 'back.out(2.2)'), out),
    'scale-pop': (c, o, at, d, e, out) => PRESETS.pop(c, o, at, d, e, out),
    blur: (c, o, at, d, e, out) => tw(c.A, {opacity: 0, filter: `blur(${px(o.amount, H, 24 * U)}px)`, scale: 1.06}, at, d, e || 'power3.out', out),
    elastic: (c, o, at, d, e, out) => tw(c.A, {scale: 0}, at, D(o, 1.2), e || 'elastic.out(1, 0.45)', out),
    bounce: (c, o, at, d, e, out) => tw(c.A, {y: -px(o.distance, H, H * 0.6), opacity: out ? 0 : 1}, at, D(o, 1.1), e || (out ? 'power3.in' : 'bounce.out'), out),
    flip: (c, o, at, d, e, out) => tw(c.A, {[o.axis === 'x' ? 'rotationX' : 'rotationY']: o.angle ?? 90, opacity: 0, transformPerspective: 1400 * U}, at, d, e || 'power3.out', out),
    'flip-3d': (c, o, at, d, e, out) => PRESETS.flip(c, o, at, d, e, out),
    spin: (c, o, at, d, e, out) => tw(c.A, {rotation: o.angle ?? -180, scale: 0, opacity: 0}, at, d, e || 'back.out(1.6)', out),
    swing: (c, o, at, d, e, out) => { gsap.set(c.A, {transformOrigin: '50% 0%'}); return tw(c.A, {rotation: o.angle ?? -25, opacity: 0}, at, D(o, 1.3), e || 'elastic.out(1, 0.4)', out); },
    wipe: (c, o, at, d, e, out) => {
      const from = o.from || o.direction || (c.rtl ? 'right' : 'left');
      if (out) return master.fromTo(c.A, {clipPath: CLIP.full}, {clipPath: CLIP[{left: 'right', right: 'left', top: 'bottom', bottom: 'top'}[from]], duration: d, ease: e || 'expo.inOut'}, at);
      return master.fromTo(c.A, {clipPath: CLIP[from]}, {clipPath: CLIP.full, duration: d, ease: e || 'expo.inOut'}, at);
    },
    mask: (c, o, at, d, e, out) => PRESETS.wipe(c, o, at, d, e, out),
    'clip-reveal': (c, o, at, d, e, out) => PRESETS.wipe(c, o, at, d, e, out),
    iris: (c, o, at, d, e, out) => {
      const a = 'circle(0% at 50% 50%)', b = 'circle(75% at 50% 50%)';
      return out ? master.fromTo(c.A, {clipPath: b}, {clipPath: a, duration: d, ease: e || 'expo.in'}, at)
                 : master.fromTo(c.A, {clipPath: a}, {clipPath: b, duration: d, ease: e || 'expo.out'}, at);
    },
    block: (c, o, at, d, e, out) => {  // colour block wipes on, content swaps in under it, block wipes off
      const b = document.createElement('div'); b.className = 'block'; b.style.background = color(o.color, C.primary);
      c.A.appendChild(b);
      const from = o.from || (c.rtl ? 'right' : 'left'), o1 = from === 'left' ? '0% 50%' : '100% 50%', o2 = from === 'left' ? '100% 50%' : '0% 50%';
      const h = d / 2;
      master.fromTo(b, {scaleX: 0, transformOrigin: o1}, {scaleX: 1, duration: h, ease: 'expo.inOut', immediateRender: false}, at);
      master.set(b, {transformOrigin: o2}, at + h);
      master.to(b, {scaleX: 0, duration: h, ease: 'expo.inOut'}, at + h);
      const kids = [...c.A.children].filter(k => k !== b);
      if (out) master.to(kids, {opacity: 0, duration: 0.01}, at + h);
      else master.fromTo(kids, {opacity: 0}, {opacity: 1, duration: 0.01, immediateRender: true}, at + h);
    },
    'split-chars': (c, o, at, d, e, out) => split(c, o, at, d, e, out, 'chars'),
    'split-words': (c, o, at, d, e, out) => split(c, o, at, d, e, out, 'words'),
    'split-lines': (c, o, at, d, e, out) => split(c, o, at, d, e, out, 'lines'),
    typewriter: (c, o, at, d, e, out) => {
      const unit = c.rtl ? 'words' : 'chars';
      const t = textTargets(c, unit, false);
      const cps = o.speed ?? (unit === 'chars' ? 0.045 : 0.2);
      const caret = document.createElement('span'); caret.className = 'caret'; caret.style.background = color(o.caret_color, C.primary);
      c.C.appendChild(caret);
      t.forEach((el, i) => { master.set(el, {display: 'none'}, 0); master.set(el, {display: 'inline-block'}, at + i * cps); });
      if (o.caret !== false) {
        const end = at + t.length * cps;
        const until = o.caret_until != null ? T(o.caret_until, c) : c.outAbs;
        for (let k = 0; end + k * 0.5 < until; k++) master.set(caret, {opacity: k % 2 ? 1 : 0}, end + k * 0.5 + 0.25);
      } else caret.style.display = 'none';
    },
    scramble: (c, o, at, d, e, out) => {
      if (c.rtl || !window.ScrambleTextPlugin) return split(c, Object.assign({style: 'fade'}, o), at, d, e, out, 'words');
      const final = c.C.innerHTML, plain = c.plain;
      master.fromTo(c.C, {text: ''}, {duration: D(o, Math.min(2, 0.3 + plain.length * 0.04)), ease: 'none',
        scrambleText: {text: plain, chars: o.chars || 'upperCase', revealDelay: o.reveal_delay ?? 0.35, speed: 0.7, tweenLength: false}}, at);
      master.set(c.C, {innerHTML: final}, at + D(o, Math.min(2, 0.3 + plain.length * 0.04)) + 0.001);
    },
    highlight: (c, o, at, d, e, out) => {
      let targets = [...c.C.querySelectorAll('.emph')];
      if (!targets.length) targets = [c.C];
      targets.forEach((t, i) => {
        let hl = t.querySelector(':scope > .hl');
        if (!hl) { hl = document.createElement('span'); hl.className = 'hl'; hl.style.background = color(o.color, C.accent); t.prepend(hl); if (t === c.C) Object.assign(hl.style, {left: '-.1em', right: '-.1em'}); }
        if (o.text_color || t !== c.C) t.style.color = color(o.text_color, C.background);
        master.fromTo(hl, {scaleX: 0, transformOrigin: c.rtl ? '100% 50%' : '0% 50%'}, {scaleX: 1, duration: d, ease: e || 'expo.inOut'}, at + i * (o.stagger ?? 0.15));
      });
    },
    underline: (c, o, at, d, e, out) => {
      let targets = [...c.C.querySelectorAll('.emph')];
      if (!targets.length || o.whole) targets = [c.C];
      targets.forEach((t, i) => {
        const u = document.createElement('span'); u.className = 'uline'; u.style.background = color(o.color, C.primary);
        if (o.thickness) u.style.height = o.thickness;
        t.style.position = 'relative'; t.appendChild(u);
        master.fromTo(u, {scaleX: 0, transformOrigin: c.rtl ? '100% 50%' : '0% 50%'}, {scaleX: 1, duration: d, ease: e || 'expo.inOut'}, at + i * 0.15);
      });
    },
    shine: (c, o, at, d, e, out) => {
      const band = color(o.color, c.isText ? 'rgba(255,255,255,.85)' : 'rgba(255,255,255,.42)');
      if (c.isText) {
        const s = document.createElement('div'); s.className = 'shine';
        Object.assign(s.style, {font: 'inherit', textAlign: getComputedStyle(c.C).textAlign, lineHeight: getComputedStyle(c.C).lineHeight,
          letterSpacing: getComputedStyle(c.C).letterSpacing, padding: getComputedStyle(c.C).padding, whiteSpace: getComputedStyle(c.C).whiteSpace,
          backgroundImage: `linear-gradient(105deg, transparent 40%, ${band} 50%, transparent 60%)`});
        s.innerHTML = esc(c.plain); s.dir = c.C.dir; c.A.appendChild(s);
        master.fromTo(s, {backgroundPosition: '130% 0'}, {backgroundPosition: '-30% 0', duration: D(o, 1.1), ease: e || 'power2.inOut'}, at);
      } else {
        const wrap = document.createElement('div'); Object.assign(wrap.style, {position: 'absolute', inset: 0, overflow: 'hidden', pointerEvents: 'none'});
        const mimg = c.o.svg ? `url("data:image/svg+xml;utf8,${encodeURIComponent(c.o.svg)}")` : null;
        if (mimg) Object.assign(wrap.style, {webkitMaskImage: mimg, maskImage: mimg, webkitMaskSize: c.o.fit === 'cover' ? 'cover' : 'contain', maskSize: c.o.fit === 'cover' ? 'cover' : 'contain', webkitMaskRepeat: 'no-repeat', maskRepeat: 'no-repeat', webkitMaskPosition: 'center', maskPosition: 'center'});
        if (c.o.radius != null) wrap.style.borderRadius = c.C.style.borderRadius;
        const im = c.type === 'image' && c.C.querySelector('img');
        if (im && (c.o.fit || 'contain') === 'contain') {  // the band only crosses the visible picture, not the letterbox
          const fitRect = () => { const bw = c.C.offsetWidth, bh = c.C.offsetHeight, r = (im.naturalWidth || 1) / (im.naturalHeight || 1);
            let w = bw, h = bw / r; if (h > bh) { h = bh; w = bh * r; }
            Object.assign(wrap.style, {inset: 'auto', left: (bw - w) / 2 + 'px', top: (bh - h) / 2 + 'px', width: w + 'px', height: h + 'px'}); };
          if (im.complete) fitRect(); else im.addEventListener('load', fitRect, {once: true});
        }
        const b = document.createElement('div'); b.className = 'shineband'; b.style.background = `linear-gradient(90deg, transparent 0%, ${band} 50%, transparent 100%)`;  // angle comes from the CSS skew (a 100deg gradient on a tall band looks flat)
        wrap.appendChild(b); c.A.appendChild(wrap);
        master.fromTo(b, {left: '-45%'}, {left: '115%', duration: D(o, 1.1), ease: e || 'power2.inOut'}, at);
      }
    },
    glitch: (c, o, at, d, e, out) => {
      const n = Math.max(4, Math.round(D(o, 0.5) / 0.04));
      for (let i = 0; i < n; i++) {
        const k = (n - i) / n, a = (rnd() - 0.5) * 40 * U * k, y0 = rnd() * 80, y1 = Math.min(100, y0 + 8 + rnd() * 30);
        master.set(c.A, {x: a, skewX: (rnd() - 0.5) * 20 * k, clipPath: rnd() < 0.5 ? `inset(${y0}% -10% ${100 - y1}% -10%)` : 'inset(-30% -30% -30% -30%)',
          filter: `drop-shadow(${6 * U * k}px 0 ${C.accent}) drop-shadow(${-6 * U * k}px 0 #00E5FF)`, opacity: out ? k : 1}, at + i * 0.04);
      }
      master.set(c.A, {x: 0, skewX: 0, clipPath: 'inset(-30% -30% -30% -30%)', filter: 'none', opacity: out ? 0 : 1}, at + n * 0.04);
      if (!out && at > 0) master.set(c.A, {opacity: 0}, 0), master.set(c.A, {opacity: 1}, at);
    },
    draw: (c, o, at, d, e, out) => {
      const els = [...c.C.querySelectorAll('path,line,polyline,polygon,circle,rect,ellipse')];
      if (!els.length || !window.DrawSVGPlugin) return PRESETS.wipe(c, o, at, d, e, out);
      const sw = px(o.stroke_width, H, 3 * U);
      els.forEach((el, i) => {
        const fill = el.getAttribute('fill');
        const hasFill = fill && fill !== 'none';
        if (!el.getAttribute('stroke') || el.getAttribute('stroke') === 'none') {
          el.setAttribute('stroke', hasFill && !fill.startsWith('url') ? fill : color(o.color, C.primary)); el.setAttribute('stroke-width', sw / Math.max(0.01, svgScale(el)));
          el.setAttribute('stroke-linecap', 'round'); el.setAttribute('stroke-linejoin', 'round');
          if (hasFill && o.keep_stroke !== true) master.to(el, {attr: {'stroke-opacity': 0}, duration: 0.5}, at + d + 0.3 + i * (o.stagger ?? 0.06));
        }
        const st = at + i * (o.stagger ?? 0.06);
        if (out) master.to(el, {drawSVG: '100% 100%', duration: d, ease: e || 'power2.inOut'}, st);
        else {
          master.fromTo(el, {drawSVG: '0%'}, {drawSVG: '100%', duration: d, ease: e || 'power2.inOut'}, st);
          if (hasFill && o.fill !== false) master.fromTo(el, {attr: {'fill-opacity': 0}}, {attr: {'fill-opacity': 1}, duration: 0.6, ease: 'power2.out'}, st + d * 0.75);
        }
      });
    },
    morph: (c, o, at, d, e, out) => {
      const p = c.C.querySelector('path');
      if (!p || !window.MorphSVGPlugin) return warn('morph needs a shape/svg layer with a path');
      const box = c.L.getBoundingClientRect();
      const to = /^[MmZzLlHhVvCcSsQqTtAa0-9\s.,\-]+$/.test(o.to || '') ? o.to : shapePath({shape: o.to || 'circle', radius: o.radius, points_count: o.points_count}, px(c.o.w, W, box.width), px(c.o.h, H, box.height));
      master.to(p, {morphSVG: {shape: to, shapeIndex: o.shape_index ?? 'auto'}, duration: d, ease: e || 'expo.inOut'}, at);
      if (o.fill) master.to(p, {attr: {fill: color(o.fill)}, duration: d, ease: e || 'expo.inOut'}, at);
    },
    'motion-path': (c, o, at, d, e, out) => {
      const path = typeof o.path === 'string' ? o.path : (o.path || [[0, 0], [200, -100], [400, 0]]).map(p => ({x: px(p[0], W, 0), y: px(p[1], H, 0)}));
      master.to(c.A, {motionPath: {path, curviness: o.curviness ?? 1.25, autoRotate: o.auto_rotate || false}, duration: D(o, 2), ease: e || 'power2.inOut'}, at);
    },
    counter: (c, o, at, d, e, out) => {
      const obj = {v: o.from ?? 0}, to = o.to ?? c.o.value ?? 100;
      const render = () => { c.C.textContent = counterText(c.o, obj.v, o); };
      // reserve the final width so centred numbers don't wobble while counting
      obj.v = to; render(); c.C.style.minWidth = c.C.scrollWidth + 'px';
      obj.v = o.from ?? 0; render();
      c.C.style.fontVariantNumeric = 'tabular-nums';
      master.to(obj, {v: to, duration: D(o, 1.8), ease: e || 'expo.out', onUpdate: render}, at);
    },
    // ---- continuous (loops over the layer's life, on .F so they stack with in/out presets)
    wiggle: (c, o, at, d, e) => loop(c, o, at, (t, k) => ({x: k * (Math.sin(t * 7.3 + 1) + Math.sin(t * 13.1)) * px(o.amount, H, 6 * U) / 2,
      y: k * (Math.sin(t * 8.7 + 2) + Math.sin(t * 11.9 + 4)) * px(o.amount, H, 6 * U) / 2, rotation: k * Math.sin(t * 9.1 + 3) * (o.rotate ?? 1.5)})),
    shake: (c, o, at, d, e) => PRESETS.wiggle(c, Object.assign({amount: 14 * U, rotate: 3}, o), at, d, e),
    float: (c, o, at, d, e) => loop(c, o, at, (t) => ({y: Math.sin((t - at) * Math.PI * 2 / (o.period ?? 3.2)) * px(o.amount, H, 12 * U), rotation: Math.sin((t - at) * Math.PI * 2 / (o.period ?? 3.2) * 0.7) * (o.rotate ?? 0)})),
    parallax: (c, o, at, d, e) => loop(c, o, at, (t) => ({x: (t - at) * px(o.speed_x ?? o.speed, W, -30 * U), y: (t - at) * px(o.speed_y, H, 0)})),
    pulse: (c, o, at, d, e) => loop(c, o, at, (t) => ({scale: 1 + (o.amount ?? 0.04) * (0.5 - 0.5 * Math.cos((t - at) * Math.PI * 2 / (o.period ?? 1.2)))})),
    'spin-loop': (c, o, at, d, e) => loop(c, o, at, (t) => ({rotation: (t - at) * (o.speed ?? 30)})),
    kenburns: (c, o, at, d, e) => loop(c, o, at, (t) => { const p = Math.max(0, Math.min(1, (t - at) / Math.max(0.1, c.outAbs - at)));
      return {scale: 1 + (o.amount ?? 0.12) * p, x: p * px(o.pan_x, W, -20 * U), y: p * px(o.pan_y, H, -10 * U)}; }),
  };
  function svgScale(el) { try { const m = el.getScreenCTM(); return m ? Math.hypot(m.a, m.b) : 1; } catch (e) { return 1; } }
  function tw(target, vars, at, d, e, out) {
    if (out) return master.to(target, Object.assign({}, vars, {duration: d, ease: e || 'power3.in'}), at);
    return master.from(target, Object.assign({}, vars, {duration: d, ease: e || 'expo.out', immediateRender: true}), at);
  }
  function split(c, o, at, d, e, out, unit) {
    if (!c.isText) return PRESETS[o.style || 'rise'](c, o, at, d, e, out);
    const style = o.style || 'rise';
    const masked = o.mask ?? (style === 'rise' || style === 'drop');
    const t = textTargets(c, unit === 'lines' ? 'words' : (unit === 'chars' && c.rtl ? 'words' : unit), masked);
    const stg = staggerFor(c, t, o, unit === 'chars' && c.rtl ? 'words' : unit);
    const V = {
      rise: {yPercent: 110}, drop: {yPercent: -110}, fade: {opacity: 0}, pop: {scale: 0, opacity: 0}, blur: {opacity: 0, filter: `blur(${12 * U}px)`, y: 20 * U},
      rotate: {rotationX: -90, opacity: 0, transformOrigin: '50% 100%', transformPerspective: 800 * U}, slide: {x: (c.rtl ? -1 : 1) * 60 * U, opacity: 0},
      scale: {scale: 1.8, opacity: 0}, 'rise-fade': {y: 50 * U, opacity: 0}, elastic: {scale: 0, opacity: 0}, punch: {scale: 2.2, opacity: 0, filter: `blur(${10 * U}px)`},
    }[style] || {opacity: 0};
    const ee = e || ({pop: 'back.out(2.5)', elastic: 'elastic.out(1, 0.5)', punch: 'expo.out'}[style]) || 'expo.out';
    if (out) {
      const isMasked = t[0] && t[0].parentElement && t[0].parentElement.classList.contains('swm');
      const OV = style === 'rise' ? Object.assign({yPercent: -110}, isMasked ? {} : {opacity: 0}) : V;
      return master.to(t, Object.assign({}, OV, {duration: d, ease: e || 'power3.in', stagger: stg}), at);
    }
    return master.from(t, Object.assign({}, V, {duration: d, ease: ee, stagger: stg, immediateRender: true}), at);
  }
  function loop(c, o, at, fn) {
    const end = o.until != null ? T(o.until, c) : c.outAbs;
    const obj = {t: at};
    master.fromTo(obj, {t: at}, {t: end, duration: Math.max(0.01, end - at), ease: 'none', immediateRender: false,
      onUpdate: () => gsap.set(c.F, fn(obj.t, 1))}, at);
  }

  function applyAnims(ctx) {
    const o = ctx.o;
    let list = [];
    const norm = (a, dir) => typeof a === 'string' ? {preset: a, dir} : Object.assign({dir}, a);
    if (o.enter) list.push(norm(o.enter, 'in'));
    for (const a of (o.animate || o.animations || [])) list.push(norm(a, null));
    if (o.loop_anim) list.push(norm(o.loop_anim, 'loop'));
    if (o.exit) list.push(norm(o.exit, 'out'));
    if (ctx.type === 'counter' && !list.some(a => (a.preset || a.name) === 'counter')) list.push({preset: 'counter', at: 0.2});
    for (const a of list) {
      let name = a.preset || a.name || 'fade';
      let out = a.dir === 'out';
      const m = /^(.*)-(in|out)$/.exec(name);
      if (m && PRESETS[m[1]]) { name = m[1]; out = m[2] === 'out'; }
      const fn = PRESETS[name];
      if (!fn) { warn('unknown preset ' + name); continue; }
      const d = a.duration ?? (out ? 0.5 : 0.9);
      let at;
      if (a.at != null) at = T(a.at, ctx);
      else at = out ? ctx.outAbs - d : ctx.inAbs;
      try { fn(ctx, a, at, d, a.ease ? ez(a.ease) : null, out); } catch (err) { warn(`preset ${name}: ${err}`); }
    }
    // custom keyframes on .L: [{t, x, y, scale, rotation, opacity, ease}] — x/y are canvas positions of the anchor
    const kf = o.keyframes || [];
    for (let i = 1; i < kf.length; i++) {
      const a = kf[i - 1], b = kf[i];
      const conv = (k) => { const v = {}; for (const [key, val] of Object.entries(k)) {
        if (key === 't' || key === 'ease') continue;
        if (key === 'x') v.x = px(val, W, ctx.bx); else if (key === 'y') v.y = px(val, H, ctx.by);
        else if (key === 'rotate') v.rotation = val; else v[key] = val; } return v; };
      const ta = T(a.t, ctx), tb = T(b.t, ctx);
      master.fromTo(ctx.L, conv(a), Object.assign(conv(b), {duration: Math.max(0.001, tb - ta), ease: ez(b.ease, 'power2.inOut'), immediateRender: i === 1}), ta);
    }
    // raw gsap tweens: [{target:'L'|'F'|'A'|'C'|css selector, from:{}, to:{}, at, duration, ease, stagger}]
    for (const g of (o.tweens || [])) {
      let tgt = {L: ctx.L, F: ctx.F, A: ctx.A, C: ctx.C}[g.target || 'A'];
      if (!tgt) tgt = ctx.C.querySelectorAll(g.target);
      const at = T(g.at ?? 0, ctx), vars = Object.assign({duration: g.duration ?? 0.8, ease: ez(g.ease, 'expo.out')}, g.stagger ? {stagger: g.stagger} : {});
      if (g.from && g.to) master.fromTo(tgt, g.from, Object.assign({}, g.to, vars), at);
      else if (g.from) master.from(tgt, Object.assign({}, g.from, vars, {immediateRender: true}), at);
      else master.to(tgt, Object.assign({}, g.to || {}, vars), at);
    }
  }

  // ------------------------------------------------------------------ backgrounds, camera, transitions, post
  function background(el, b, start, dur) {
    if (SPEC.transparent && (b == null || b === 'none')) return;
    b = b ?? 'background';
    if (b === 'none' || b === 'transparent') return;
    if (typeof b === 'string' && !/^(animated|mesh|grid|dots)$/.test(b) && !/\.(png|jpe?g|webp|mp4|mov|webm)$/i.test(b)) { el.style.background = color(b); return; }
    const o = typeof b === 'string' ? {type: b} : b;
    if (o.gradient || o.colors || o.linear || o.radial) { el.style.background = gradient(o.gradient || o); }
    else el.style.background = color(o.color, C.background);
    if (o.src) {
      const m = document.createElement(o.video ? 'video' : 'img'); m.src = o.src;
      Object.assign(m.style, {inset: 0, width: '100%', height: '100%', objectFit: 'cover', opacity: o.opacity ?? 1});
      if (o.video) { m.muted = true; m.dataset.start = String(start); m.dataset.loop = 'true'; waits.push(new Promise(r => { m.addEventListener('loadeddata', r, {once: true}); setTimeout(r, 15000); })); }
      el.appendChild(m);
      if (o.kenburns !== false) master.fromTo(m, {scale: 1.0}, {scale: 1.1, duration: dur, ease: 'none', immediateRender: false}, start);
      if (o.dim) { const d = document.createElement('div'); Object.assign(d.style, {inset: 0, background: `rgba(0,0,0,${o.dim})`}); el.appendChild(d); }
    }
    if (o.type === 'animated' || o.type === 'mesh' || o.blobs) {
      const cols = (o.blob_colors || ['primary', 'accent', 'primary']).map(c => color(c));
      cols.forEach((c, i) => {
        const bl = document.createElement('div');
        const s = (1100 + i * 250) * U;
        Object.assign(bl.style, {width: s + 'px', height: s + 'px', borderRadius: '50%', left: [-0.15, 0.55, 0.2][i % 3] * W - s / 4 + 'px', top: [-0.35, 0.35, 0.6][i % 3] * H - s / 4 + 'px',
          background: `radial-gradient(closest-side, ${c}, transparent)`, opacity: o.intensity ?? [0.34, 0.22, 0.16][i % 3]});
        el.appendChild(bl);
        master.fromTo(bl, {x: 0, y: 0, scale: 1}, {x: (i % 2 ? -1 : 1) * 160 * U, y: (i % 2 ? 1 : -1) * 90 * U, scale: 1.12, duration: Math.max(1, dur), ease: 'sine.inOut', immediateRender: false}, start);
      });
    }
    if (o.type === 'grid' || o.type === 'dots' || o.pattern) {
      const p = document.createElement('div'); const pc = rgba(color(o.pattern_color, C.text), o.pattern_opacity ?? 0.07); const g = (o.spacing ?? 64) * U;
      Object.assign(p.style, {inset: '-10%', backgroundSize: `${g}px ${g}px`,
        backgroundImage: (o.pattern || o.type) === 'dots' ? `radial-gradient(${pc} ${1.6 * U}px, transparent ${2 * U}px)` : `linear-gradient(${pc} ${1.2 * U}px, transparent ${1.2 * U}px), linear-gradient(90deg, ${pc} ${1.2 * U}px, transparent ${1.2 * U}px)`,
        maskImage: 'radial-gradient(ellipse at center, #000 30%, transparent 80%)', webkitMaskImage: 'radial-gradient(ellipse at center, #000 30%, transparent 80%)'});
      el.appendChild(p);
      master.fromTo(p, {x: 0}, {x: -g, y: -g * 0.5, duration: Math.max(1, dur), ease: 'none', immediateRender: false}, start);
    }
  }
  function camera(el, cam, start, dur) {
    if (!cam) return;
    const o = typeof cam === 'string' ? {preset: cam} : cam;
    if (o.keyframes) {
      const kf = o.keyframes;
      for (let i = 1; i < kf.length; i++) {
        const a = Object.assign({}, kf[i - 1]), b = Object.assign({}, kf[i]);
        const ta = start + (a.t || 0), tb = start + (b.t || 0), e = ez(b.ease, 'power2.inOut');
        delete a.t; delete a.ease; delete b.t; delete b.ease;
        master.fromTo(el, a, Object.assign(b, {duration: Math.max(0.001, tb - ta), ease: e, immediateRender: i === 1}), ta);
      }
      return;
    }
    const amt = o.amount, at = start + (o.at || 0), d = o.duration ?? dur - (o.at || 0), e = ez(o.ease, 'power1.inOut');
    const P = {
      'push-in': [{scale: 1}, {scale: 1 + (amt ?? 0.08)}], 'pull-out': [{scale: 1 + (amt ?? 0.08)}, {scale: 1}],
      'zoom-in': [{scale: 1}, {scale: 1 + (amt ?? 0.25)}], 'zoom-out': [{scale: 1 + (amt ?? 0.25)}, {scale: 1}],
      'pan-left': [{x: (amt ?? 0.04) * W}, {x: -(amt ?? 0.04) * W}], 'pan-right': [{x: -(amt ?? 0.04) * W}, {x: (amt ?? 0.04) * W}],
      'tilt-up': [{y: -(amt ?? 0.04) * H}, {y: (amt ?? 0.04) * H}], 'tilt-down': [{y: (amt ?? 0.04) * H}, {y: -(amt ?? 0.04) * H}],
      drift: [{x: -0.012 * W, y: 0.008 * H, rotation: -0.4, scale: 1.03}, {x: 0.012 * W, y: -0.008 * H, rotation: 0.4, scale: 1.06}],
      orbit: [{rotationY: -(amt ?? 6), scale: 1.04, transformPerspective: 1800 * U}, {rotationY: (amt ?? 6), scale: 1.04, transformPerspective: 1800 * U}],
      roll: [{rotation: -(amt ?? 3), scale: 1.08}, {rotation: (amt ?? 3), scale: 1.08}],
    }[o.preset];
    if (P) master.fromTo(el, P[0], Object.assign({}, P[1], {duration: Math.max(0.01, d), ease: o.preset === 'drift' || o.preset === 'orbit' ? 'sine.inOut' : e, immediateRender: true}), at);
    if (o.preset === 'handheld' || o.preset === 'shake' || o.shake) {
      const k = o.preset === 'shake' ? (amt ?? 1) * 3 : (amt ?? 1);
      const obj = {t: 0};
      master.fromTo(obj, {t: 0}, {t: d, duration: Math.max(0.01, d), ease: 'none', immediateRender: false, onUpdate: () => {
        const t = obj.t; gsap.set(el, {x: k * 4 * U * (Math.sin(t * 2.1) + 0.6 * Math.sin(t * 5.3 + 1)), y: k * 3 * U * (Math.sin(t * 1.7 + 2) + 0.5 * Math.sin(t * 4.9)),
          rotation: k * 0.25 * Math.sin(t * 1.3 + 0.5), scale: 1.02}); }}, at);
    }
    if (o.punch) for (const p of o.punch) {  // quick zoom bumps on beats: punch: [1.2, 2.4] (seconds in the scene)
      master.to(el, {scale: '+=' + (o.punch_amount ?? 0.06), duration: 0.08, ease: 'power2.out'}, start + p);
      master.to(el, {scale: '-=' + (o.punch_amount ?? 0.06), duration: 0.5, ease: 'expo.out'}, start + p + 0.08);
    }
  }
  function transition(prev, next, tr, at) {
    const o = typeof tr === 'string' ? {type: tr} : tr;
    const d = o.duration ?? 0.7, type = o.type || 'crossfade', dir = o.direction || 'left', e = ez(o.ease, 'expo.inOut');
    const sgn = dir === 'right' || dir === 'down' ? -1 : 1, vert = dir === 'up' || dir === 'down';
    const off = vert ? {y: sgn * H} : {x: sgn * W}, offN = vert ? {y: -sgn * H} : {x: -sgn * W};
    const mid = at + d / 2;
    switch (type) {
      case 'cut': break;
      case 'crossfade': case 'fade': master.fromTo(next, {opacity: 0}, {opacity: 1, duration: d, ease: 'power1.inOut', immediateRender: false}, at); break;
      case 'dip': {
        const ov = document.createElement('div'); ov.className = 'overlay'; ov.style.background = color(o.color, '#000'); ov.style.zIndex = 70; comp.appendChild(ov);
        master.fromTo(ov, {opacity: 0}, {opacity: 1, duration: d / 2, ease: 'power2.in', immediateRender: true}, at);
        master.to(ov, {opacity: 0, duration: d / 2, ease: 'power2.out'}, mid);
        master.set(next, {opacity: 0}, 0); master.set(next, {opacity: 1}, mid); break;
      }
      case 'wipe': {
        const from = {left: 'right', right: 'left', up: 'bottom', down: 'top'}[dir] || 'right';
        master.fromTo(next, {clipPath: CLIP[from].replace(/-30%/g, '0%')}, {clipPath: 'inset(0% 0% 0% 0%)', duration: d, ease: e, immediateRender: false}, at);
        if (o.edge !== false) {
          const bar = document.createElement('div'); bar.className = 'tpanel'; bar.style.background = color(o.color, C.primary);
          Object.assign(bar.style, vert ? {left: '-10%', right: '-10%', top: 0, bottom: 'auto', height: 14 * U + 'px'} : {width: 14 * U + 'px'});
          comp.appendChild(bar);
          const a0 = vert ? {y: sgn > 0 ? H : -14 * U} : {x: sgn > 0 ? W : -14 * U}, a1 = vert ? {y: sgn > 0 ? -14 * U : H} : {x: sgn > 0 ? -14 * U : W};
          master.set(bar, {opacity: 0}, 0); master.set(bar, {opacity: 1}, at);
          master.fromTo(bar, a0, Object.assign({}, a1, {duration: d, ease: e, immediateRender: false}), at); master.set(bar, {opacity: 0}, at + d);
        }
        break;
      }
      case 'slide': master.fromTo(next, off, {x: 0, y: 0, duration: d, ease: e, immediateRender: false}, at); break;
      case 'push': master.fromTo(next, off, {x: 0, y: 0, duration: d, ease: e, immediateRender: false}, at);
        master.fromTo(prev, {x: 0, y: 0}, Object.assign({}, offN, {duration: d, ease: e, immediateRender: false}), at); break;
      case 'zoom': master.fromTo(prev, {scale: 1, opacity: 1, filter: 'blur(0px)'}, {scale: 1.6, opacity: 0, filter: `blur(${18 * U}px)`, duration: d, ease: 'power3.in', immediateRender: false}, at);
        master.fromTo(next, {scale: 0.75, opacity: 0, filter: `blur(${18 * U}px)`}, {scale: 1, opacity: 1, filter: 'blur(0px)', duration: d, ease: 'expo.out', immediateRender: false}, at + d * 0.35); break;
      case 'blur': master.fromTo(prev, {filter: 'blur(0px)', opacity: 1}, {filter: `blur(${30 * U}px)`, opacity: 0, duration: d, ease: 'power2.in', immediateRender: false}, at);
        master.fromTo(next, {filter: `blur(${30 * U}px)`, opacity: 0}, {filter: 'blur(0px)', opacity: 1, duration: d, ease: 'power2.out', immediateRender: false}, at + d * 0.3); break;
      case 'iris': master.fromTo(next, {clipPath: `circle(0% at ${o.at || '50% 50%'})`}, {clipPath: `circle(80% at ${o.at || '50% 50%'})`, duration: d, ease: e, immediateRender: false}, at); break;
      case 'whip': {  // whip pan: both scenes fly sideways with directional blur
        const fid = 'whip' + Math.round(at * 1000);
        const svgNS = 'http://www.w3.org/2000/svg';
        const defs = document.getElementById('fx');
        const f = document.createElementNS(svgNS, 'filter'); f.id = fid; f.setAttribute('x', '-20%'); f.setAttribute('width', '140%');
        const gb = document.createElementNS(svgNS, 'feGaussianBlur'); gb.setAttribute('stdDeviation', '0 0'); f.appendChild(gb); defs.appendChild(f);
        const bx = {v: 0};
        const upd = () => gb.setAttribute('stdDeviation', vert ? `0 ${bx.v}` : `${bx.v} 0`);
        master.set([prev, next], {filter: `url(#${fid})`}, at); master.set([prev, next], {filter: 'none'}, at + d);
        master.fromTo(bx, {v: 0}, {v: 70 * U, duration: d / 2, ease: 'power2.in', onUpdate: upd, immediateRender: false}, at);
        master.to(bx, {v: 0, duration: d / 2, ease: 'power2.out', onUpdate: upd}, mid);
        master.fromTo(prev, {x: 0, y: 0}, Object.assign({}, vert ? {y: -sgn * H * 0.6} : {x: -sgn * W * 0.6}, {duration: d / 2, ease: 'power3.in', immediateRender: false}), at);
        master.set(prev, {opacity: 0}, mid);
        master.fromTo(next, Object.assign({opacity: 0}, vert ? {y: sgn * H * 0.6} : {x: sgn * W * 0.6}), {x: 0, y: 0, opacity: 1, duration: d / 2, ease: 'power3.out', immediateRender: false}, mid);
        break;
      }
      case 'shape': {
        const cols = (o.colors || ['accent', 'primary', 'panel']).map(c => color(c));
        const panels = cols.map((c, i) => { const p = document.createElement('div'); p.className = 'tpanel'; p.style.background = c;
          Object.assign(p.style, {left: 0, width: W * 1.3 + 'px'}); comp.appendChild(p); return p; });
        panels.forEach((p, i) => {
          gsap.set(p, {x: sgn * W * 1.15, skewX: -14, opacity: 0});
          master.set(p, {opacity: 0, x: sgn * W * 1.15}, 0); master.set(p, {opacity: 1}, at);
          master.fromTo(p, {x: sgn * W * 1.15, skewX: -14}, {x: -W * 0.15, skewX: -14, duration: d / 2, ease: 'expo.inOut', immediateRender: false}, at + i * d * 0.06);
          master.to(p, {x: -sgn * W * 1.45, duration: d / 2, ease: 'expo.inOut'}, mid + (panels.length - 1 - i) * d * 0.06);
          master.set(p, {opacity: 0}, at + d + 0.2);
        });
        master.set(next, {opacity: 0}, 0); master.set(next, {opacity: 1}, mid + 0.02); break;
      }
      case 'glitch': {
        for (let i = 0; i < 8; i++) { const tt = at + i * d / 8, k = 1 - Math.abs(i - 4) / 4;
          master.set(i < 4 ? prev : next, {x: (rnd() - 0.5) * 60 * U * k, filter: `drop-shadow(${10 * U * k}px 0 ${C.accent}) drop-shadow(${-10 * U * k}px 0 #00E5FF)`}, tt); }
        master.set([prev, next], {x: 0, filter: 'none'}, at + d);
        master.set(next, {opacity: 0}, 0); master.set(next, {opacity: 1}, mid); break;
      }
      default: warn('unknown transition ' + type);
        master.fromTo(next, {opacity: 0}, {opacity: 1, duration: d, ease: 'power1.inOut', immediateRender: false}, at);
    }
  }
  function post(p) {
    if (!p) return;
    if (p.vignette) { const v = document.createElement('div'); v.className = 'overlay vignette'; v.style.zIndex = 95; v.style.opacity = Math.min(1, p.vignette * 1.6); comp.appendChild(v); }
    if (p.light_leaks) {
      const lk = typeof p.light_leaks === 'object' ? p.light_leaks : {};
      const cols = (lk.colors || ['#FF7A2F', '#FFC46B', '#FF3D7F']).map(c => color(c));
      cols.forEach((c, i) => {
        const b = document.createElement('div'); b.className = 'leak'; const s = (900 + i * 300) * U;
        Object.assign(b.style, {width: s + 'px', height: s * 0.7 + 'px', background: `radial-gradient(closest-side, ${c}, transparent)`, zIndex: 94, opacity: 0});
        comp.appendChild(b);
        const ph = i * 1.7;
        const obj = {t: 0};
        master.fromTo(obj, {t: 0}, {t: TOTAL, duration: TOTAL, ease: 'none', immediateRender: true, onUpdate: () => {
          const t = obj.t; const a = Math.max(0, Math.sin(t * 0.55 + ph)) * (lk.intensity ?? 0.38);
          gsap.set(b, {x: (0.5 + 0.55 * Math.sin(t * 0.21 + ph)) * W - s / 2, y: (0.3 + 0.5 * Math.cos(t * 0.17 + ph * 2)) * H - s * 0.35, opacity: a});
        }}, 0);
      });
    }
    if (p.letterbox) {
      const ratio = typeof p.letterbox === 'number' ? p.letterbox : 2.39;
      const bh = Math.max(0, (H - W / ratio) / 2);
      for (const side of ['top', 'bottom']) {
        const b = document.createElement('div'); b.className = 'letterbox'; b.style[side] = 0; b.style.height = bh + 'px'; comp.appendChild(b);
        master.from(b, {height: 0, duration: 1.2, ease: 'expo.inOut', immediateRender: true}, 0);
      }
    }
  }

  // ------------------------------------------------------------------ assemble scenes
  const scenes = SPEC.scenes;
  let t0 = 0;
  const timing = scenes.map((s, i) => {
    const tr = i > 0 ? (typeof s.transition === 'string' ? {type: s.transition} : s.transition || null) : null;
    const ov = tr && tr.type !== 'cut' ? (tr.duration ?? 0.7) : 0;
    const start = i === 0 ? 0 : t0 - ov;
    t0 = start + s.duration;
    return {start, end: start + s.duration, tr};
  });
  const TOTAL = SPEC.duration || t0;
  window.__duration = TOTAL;
  const layers = [];
  const BYID = {};
  const sceneEls = [];
  scenes.forEach((s, i) => {
    const {start, end} = timing[i];
    const el = document.createElement('div'); el.className = 'scene'; el.dataset.index = i; el.style.zIndex = i + 1; world.appendChild(el);
    const bg = document.createElement('div'); bg.className = 'bg'; el.appendChild(bg);
    background(bg, s.background ?? SPEC.background, start, s.duration);
    const cam = document.createElement('div'); cam.className = 'cam'; el.appendChild(cam);
    if (s.perspective !== false) el.style.perspective = (s.perspective || 2000) * U + 'px';
    const sctx = {start, end, index: i};
    for (const lo of (s.layers || [])) build(lo, cam, sctx, start, end);
    camera(cam, s.camera, start, s.duration);
    if (i > 0) master.set(el, {visibility: 'hidden'}, 0), master.set(el, {visibility: 'inherit'}, start);
    if (i < scenes.length - 1) master.set(el, {visibility: 'hidden'}, end + 0.0001);
    sceneEls.push(el);
  });
  // layer animations after the whole DOM exists (line splitting needs final layout)
  await Promise.all(waits.splice(0));
  for (const ctx of layers) applyAnims(ctx);
  for (let i = 1; i < scenes.length; i++) if (timing[i].tr) transition(sceneEls[i - 1], sceneEls[i], timing[i].tr, timing[i].start);
  camera(world, SPEC.camera, 0, TOTAL);
  post(SPEC.post);
  if (SPEC.fade_in) master.from(comp, {opacity: 0, duration: SPEC.fade_in, ease: 'power1.out', immediateRender: true}, 0);
  if (SPEC.fade_out) master.to(comp, {opacity: 0, duration: SPEC.fade_out, ease: 'power1.in'}, TOTAL - SPEC.fade_out);
  await Promise.all(waits);
  master.time(0, false);
  window.__onseek = (t) => { master.time(t, false); for (const f of ticks) { try { f(t); } catch (e) { console.error(e); } } };
  window.__layers = layers.length;
};
