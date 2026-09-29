/* studio.js — runs inside every design page before export.
   1) fits text: every [data-fit-box] scales its [data-fit] children together (hierarchy kept)
      until nothing overflows the box, never below each element's data-min;
   2) reports: overflow, text outside the canvas / safe zone / avoid zones, broken images,
      fonts that failed, text boxes (for the contrast check done in Python).            */
(function () {
  const S = (window.__studio = window.__studio || {});
  const px = (v) => parseFloat(v) || 0;

  function overflows(box) {
    if (box.scrollHeight > box.clientHeight + 1 || box.scrollWidth > box.clientWidth + 1) return true;
    for (const el of box.querySelectorAll('[data-fit]')) {
      if (el.scrollWidth > el.clientWidth + 1) return true;           // an unbreakable word
    }
    return false;
  }

  function applyScale(els, k) {
    for (const el of els) {
      const max = px(el.dataset.max), min = px(el.dataset.min || el.dataset.max * 0.4);
      el.style.fontSize = Math.max(min, max * k).toFixed(2) + 'px';
    }
  }

  S.fit = function () {
    const results = [];
    for (const box of document.querySelectorAll('[data-fit-box]')) {
      const els = [...box.querySelectorAll('[data-fit]')];
      if (!els.length) continue;
      const grow = parseFloat(box.dataset.fitGrow || '1') || 1;
      applyScale(els, 1);
      let k = 1, lo = 0.2, hi = 1, ok = !overflows(box);
      if (ok && grow > 1) {                      // room to spare: grow up to data-fit-grow
        applyScale(els, grow);
        if (!overflows(box)) { k = grow; }
        else {
          lo = 1; hi = grow;
          for (let i = 0; i < 10; i++) { const mid = (lo + hi) / 2; applyScale(els, mid); if (overflows(box)) hi = mid; else lo = mid; }
          k = lo; applyScale(els, k);
        }
      } else if (!ok) {
        for (let i = 0; i < 14; i++) {
          const mid = (lo + hi) / 2;
          applyScale(els, mid);
          if (overflows(box)) hi = mid; else lo = mid;
        }
        k = lo;
        applyScale(els, k);
        ok = !overflows(box);
      }
      results.push({ box: box.dataset.fitBox || box.className, scale: +k.toFixed(3), ok });
    }
    return results;
  };

  function textNodesRect(el) {
    const rects = [];
    for (const n of el.childNodes) {
      if (n.nodeType === 3 && n.textContent.trim()) {
        const r = document.createRange(); r.selectNodeContents(n);
        for (const q of r.getClientRects()) if (q.width > 0 && q.height > 0) rects.push(q);
      }
    }
    if (!rects.length) return null;
    const x0 = Math.min(...rects.map(r => r.left)), y0 = Math.min(...rects.map(r => r.top));
    const x1 = Math.max(...rects.map(r => r.right)), y1 = Math.max(...rects.map(r => r.bottom));
    return { x: x0 + scrollX, y: y0 + scrollY, w: x1 - x0, h: y1 - y0 };
  }

  S.report = function (cfg) {
    cfg = cfg || {};
    const out = { overflow: [], outside: [], unsafe: [], avoid: [], images: [], fonts: [], texts: [], tiny: [] };
    const pages = [...document.querySelectorAll('.page')];
    const pageOf = (y) => { for (let i = 0; i < pages.length; i++) { const r = pages[i].getBoundingClientRect(); if (y >= r.top + scrollY - 1 && y <= r.bottom + scrollY + 1) return [i, r]; } return [0, pages[0] ? pages[0].getBoundingClientRect() : { left: 0, top: 0, width: innerWidth, height: innerHeight }]; };
    for (const box of document.querySelectorAll('[data-fit-box]')) {
      if (overflows(box)) out.overflow.push((box.dataset.fitBox || 'text') + ': ' + (box.innerText || '').slice(0, 60).replace(/\s+/g, ' '));
    }
    const all = document.querySelectorAll('.page *');
    for (const el of all) {
      const cs = getComputedStyle(el);
      if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) continue;
      const r = textNodesRect(el);
      if (!r) continue;
      const [pi, pr] = pageOf(r.y + r.h / 2);
      const x = r.x - pr.left - scrollX, y = r.y - pr.top - scrollY;
      const label = (el.innerText || el.textContent || '').trim().slice(0, 40).replace(/\s+/g, ' ');
      const fs = px(cs.fontSize);
      out.texts.push({ page: pi, x, y, w: r.w, h: r.h, color: cs.color, size: fs, weight: +cs.fontWeight || 400, text: label, decorative: el.closest('[data-decor]') ? 1 : 0, shadow: cs.textShadow !== 'none' || px(cs.webkitTextStrokeWidth) > 0 ? 1 : 0 });
      if (el.closest('[data-decor]')) continue;
      if (x < -1 || y < -1 || x + r.w > pr.width + 1 || y + r.h > pr.height + 1) out.outside.push(label);
      const s = cfg.safe;
      if (s && (x < s.left - 2 || y < s.top - 2 || x + r.w > pr.width - s.right + 2 || y + r.h > pr.height - s.bottom + 2)) out.unsafe.push(label);
      for (const a of (cfg.avoid || [])) {
        if (x < a.x + a.w && x + r.w > a.x && y < a.y + a.h && y + r.h > a.y) out.avoid.push(label + ' (' + (a.name || 'UI') + ')');
      }
      if (cfg.minText && fs < cfg.minText) out.tiny.push(label + ' @' + fs.toFixed(1) + 'px');
    }
    for (const im of document.querySelectorAll('img')) {
      if (!im.complete || im.naturalWidth === 0) out.images.push(im.getAttribute('src') || '?');
    }
    for (const f of (cfg.fonts || [])) {
      if (!document.fonts.check(f)) out.fonts.push(f);
    }
    out.fit = S.lastFit || [];
    return out;
  };

  S.hideText = function (on) {
    let st = document.getElementById('__qc_hide');
    if (on && !st) {
      st = document.createElement('style'); st.id = '__qc_hide';
      st.textContent = '.page *{color:transparent!important;text-shadow:none!important;-webkit-text-stroke-color:transparent!important;text-decoration-color:transparent!important}';
      document.head.appendChild(st);
    } else if (!on && st) st.remove();
  };

  S.run = async function (cfg) {
    cfg = cfg || {};
    await document.fonts.ready;
    try { await Promise.all((cfg.fonts || []).map(f => document.fonts.load(f))); } catch (e) {}
    await Promise.all([...document.images].map(im => im.complete ? 0 : new Promise(r => { im.onload = im.onerror = r; })));
    S.lastFit = S.fit();
    await document.fonts.ready;
    return S.report(cfg);
  };
})();
