"""Adobe After Effects: one ExtendScript (build_project.jsx) that builds the whole edit as a native AE
project and saves the .aep next to it — File › Scripts › Run Script File… (or the studio's Mac bridge
runs it for you). Everything is real AE: footage layers with in/out, speed (time stretch), position/
scale keyframes, opacity, transitions rebuilt with AE tools (opacity dissolves, dip-to-colour solids,
Linear Wipe, slide/push position moves, zoom), picture-in-picture with a rounded mask, every graphic
as its own precomp of live text layers + shape layers + images (edit the words, fonts, colours), the
captions as live text layers, the music with its ducking keyframes, markers, folders. The rendered
graphics sit underneath, switched off, as a backup. Optional: export each graphic as a .mogrt for
Premiere's Essential Graphics (set MAKE_MOGRTS = true at the top)."""
from __future__ import annotations

import json
from pathlib import Path

from .common import rel

BUILDER = r"""
// ───────────────────────── builder (ExtendScript, ES3) ─────────────────────────
(function () {
  var ROOT = new File($.fileName).parent.parent.parent;   // package root (…/Projects/AfterEffects/build_project.jsx)
  if (!ROOT.exists) { alert("Package folder not found"); return; }
  function f(rel) { return new File(ROOT.fsName + "/" + rel); }
  function hexc(h) { h = h.replace("#", ""); return [parseInt(h.substr(0, 2), 16) / 255, parseInt(h.substr(2, 2), 16) / 255, parseInt(h.substr(4, 2), 16) / 255]; }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  app.beginUndoGroup("Build " + DOC.name);
  var proj = app.project || app.newProject();
  var missing = [];
  function folder(name, parent) { var it = proj.items.addFolder(name); if (parent) it.parentFolder = parent; return it; }
  var FROOT = folder(DOC.name), FF = folder("Footage", FROOT), FA = folder("Audio", FROOT), FG = folder("Graphics", FROOT),
      FC = folder("Comps", FROOT), FX = folder("Generated", FROOT);
  var media = {};
  for (var mid in DOC.media) {
    var m = DOC.media[mid], file = f(m.rel);
    if (!file.exists) { missing.push(m.rel); continue; }
    try {
      var io = new ImportOptions(file);
      if (m.kind === "image") io.sequence = false;
      var item = proj.importFile(io);
      item.parentFolder = m.kind === "audio" ? FA : (m.kind === "graphic" || m.generated ? FG : (m.rel.indexOf("Generated") >= 0 ? FX : FF));
      if (m.alpha && item.mainSource && item.mainSource.alphaMode !== undefined) { try { item.mainSource.alphaMode = AlphaMode.STRAIGHT; } catch (e) {} }
      media[mid] = item;
    } catch (e) { missing.push(m.rel + " (" + e.toString() + ")"); }
  }
  var W = DOC.W, H = DOC.H, FPS = DOC.fps, DUR = DOC.duration;
  var comp = proj.items.addComp(DOC.name, W, H, 1, DUR, FPS); comp.parentFolder = FC;
  comp.bgColor = hexc(DOC.background || "#000000");

  function setBox(layer, src, it) {           // the whole source frame lands on it.box (pixels, top-left origin)
    var sw = src.width, sh = src.height;
    layer.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([sw / 2, sh / 2]);
    var P = layer.property("ADBE Transform Group").property("ADBE Position"), S = layer.property("ADBE Transform Group").property("ADBE Scale");
    function apply(b, t) {
      var p = [b[0] + b[2] / 2, b[1] + b[3] / 2], s = [100 * b[2] / sw, 100 * b[3] / sh];
      if (t === null) { P.setValue(p); S.setValue(s); } else { P.setValueAtTime(t, p); S.setValueAtTime(t, s); }
    }
    if (it.kf && it.kf.box) { for (var i = 0; i < it.kf.box.length; i++) apply(it.kf.box[i][1], it.rec_in + it.kf.box[i][0]); }
    else apply(it.box, null);
  }
  function opacityKf(layer, it) {
    var O = layer.property("ADBE Transform Group").property("ADBE Opacity");
    if (it.kf && it.kf.opacity) { for (var i = 0; i < it.kf.opacity.length; i++) O.setValueAtTime(it.rec_in + it.kf.opacity[i][0], 100 * it.kf.opacity[i][1]); }
    else if (it.opacity !== undefined && it.opacity < 0.999) O.setValue(100 * it.opacity);
  }
  function place(it, comp) {
    var src = media[it.mid]; if (!src) return null;
    var layer = comp.layers.add(src);
    var sp = it.speed || 1;
    if (Math.abs(sp - 1) > 1e-6) layer.stretch = 100 / sp;
    if (src.duration === 0 || src.mainSource instanceof SolidSource || !src.hasVideo && !src.hasAudio) { layer.startTime = it.rec_in; }
    else layer.startTime = it.rec_in - (it.src_in || 0) / sp;
    layer.inPoint = it.rec_in; layer.outPoint = it.rec_out;
    if (src.duration === 0) { layer.outPoint = it.rec_out; }
    layer.name = it.name;
    if (it.enabled === false) layer.enabled = false;
    return layer;
  }
  function roundedMask(layer, src, r, box) {
    if (!r) return;
    var w = src.width, h = src.height, k = 0.5523, s = new Shape();
    r = Math.min(r * w / Math.max(1, box[2]), Math.min(w, h) / 2);      // comp pixels → source pixels
    s.vertices = [[r, 0], [w - r, 0], [w, r], [w, h - r], [w - r, h], [r, h], [0, h - r], [0, r]];
    s.inTangents = [[-r * k, 0], [0, 0], [0, -r * k], [0, 0], [r * k, 0], [0, 0], [0, r * k], [0, 0]];
    s.outTangents = [[0, 0], [r * k, 0], [0, 0], [0, r * k], [0, 0], [-r * k, 0], [0, 0], [0, -r * k]];
    s.closed = true;
    var mk = layer.property("ADBE Mask Parade").addProperty("ADBE Mask Atom"); mk.property("ADBE Mask Shape").setValue(s);
  }
  function transitionIn(layer, prev, it) {
    var t = it.trans_in; if (!t) return;
    var a = it.rec_in, b = it.rec_in + t.dur, typ = t.type;
    var O = layer.property("ADBE Transform Group").property("ADBE Opacity");
    if (typ.indexOf("dip") === 0 || typ === "flash") {
      var col = (typ === "dip_white" || typ === "flash") ? [1, 1, 1] : [0, 0, 0];
      var sol = comp.layers.addSolid(col, "Dip · " + it.name, W, H, 1, t.dur); sol.startTime = a; sol.inPoint = a; sol.outPoint = b;
      var so = sol.property("ADBE Transform Group").property("ADBE Opacity");
      so.setValueAtTime(a, 0); so.setValueAtTime((a + b) / 2, 100); so.setValueAtTime(b, 0);
      O.setValueAtTime(a, 0); O.setValueAtTime((a + b) / 2 - 1e-3, 0); O.setValueAtTime((a + b) / 2, 100);
      return;
    }
    if (typ.indexOf("wipe") === 0) {
      var lw = layer.property("ADBE Effect Parade").addProperty("ADBE Linear Wipe");
      var ang = {wipe_left: 270, wipe_right: 90, wipe_up: 0, wipe_down: 180, wipe: 270}[typ] || 270;
      lw.property(2).setValue(ang); lw.property(1).setValueAtTime(a, 100); lw.property(1).setValueAtTime(b, 0); return;
    }
    if (typ.indexOf("slide") === 0 || typ === "push" || typ.indexOf("cover") === 0) {
      var P = layer.property("ADBE Transform Group").property("ADBE Position"), p = P.value;
      var off = {slide_left: [W, 0], slide_right: [-W, 0], slide_up: [0, H], slide_down: [0, -H], push: [W, 0]}[typ] || [W, 0];
      if (P.numKeys) { /* keep Ken Burns: animate a parent null instead */
        var nl = comp.layers.addNull(); nl.name = "Slide · " + it.name; nl.inPoint = a; nl.outPoint = it.rec_out;
        var NP = nl.property("ADBE Transform Group").property("ADBE Position"); var c0 = NP.value;
        layer.parent = nl; NP.setValueAtTime(a, [c0[0] + off[0], c0[1] + off[1]]); NP.setValueAtTime(b, c0);
        nl.enabled = false; nl.shy = true;
      } else { P.setValueAtTime(a, [p[0] + off[0], p[1] + off[1]]); P.setValueAtTime(b, p); }
      if (typ === "push" && prev) { var PP = prev.property("ADBE Transform Group").property("ADBE Position");
        if (!PP.numKeys) { var q = PP.value; PP.setValueAtTime(a, q); PP.setValueAtTime(b, [q[0] - off[0], q[1] - off[1]]); } }
      return;
    }
    if (typ.indexOf("zoom") === 0) {
      var S = layer.property("ADBE Transform Group").property("ADBE Scale");
      if (!S.numKeys) { var s0 = S.value; S.setValueAtTime(a, [s0[0] * 1.25, s0[1] * 1.25]); S.setValueAtTime(b, s0); }
    }
    O.setValueAtTime(a, 0); O.setValueAtTime(b, 100);            // dissolve (and every other type)
  }
  function addShape(c, l, idx) {
    var sl = c.layers.addShape(); sl.name = "Shape " + idx;
    var grp = sl.property("ADBE Root Vectors Group").addProperty("ADBE Vector Group");
    var rect = grp.property("ADBE Vectors Group").addProperty("ADBE Vector Shape - Rect");
    rect.property("ADBE Vector Rect Size").setValue([l.box[2], l.box[3]]);
    rect.property("ADBE Vector Rect Position").setValue([l.box[0] + l.box[2] / 2, l.box[1] + l.box[3] / 2]);
    if (l.radius) rect.property("ADBE Vector Rect Roundness").setValue(Math.min(l.radius, Math.min(l.box[2], l.box[3]) / 2));
    var fill = grp.property("ADBE Vectors Group").addProperty("ADBE Vector Graphic - Fill");
    fill.property("ADBE Vector Fill Color").setValue(hexc(l.color));
    if (l.border) { var st = grp.property("ADBE Vectors Group").addProperty("ADBE Vector Graphic - Stroke");
      st.property("ADBE Vector Stroke Color").setValue(hexc(l.border.color)); st.property("ADBE Vector Stroke Width").setValue(l.border.w); }
    sl.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([0, 0]);
    sl.property("ADBE Transform Group").property("ADBE Position").setValue([0, 0]);
    if (l.color2 && l.color2 !== l.color) {
      var rp = sl.property("ADBE Effect Parade").addProperty("ADBE Ramp");
      var rad = (l.angle - 90) * Math.PI / 180, cx = l.box[0] + l.box[2] / 2, cy = l.box[1] + l.box[3] / 2;
      var hx = Math.cos(rad) * l.box[2] / 2, hy = Math.sin(rad) * l.box[3] / 2;
      rp.property(1).setValue([cx - hx, cy - hy]); rp.property(2).setValue(hexc(l.color));
      rp.property(3).setValue([cx + hx, cy + hy]); rp.property(4).setValue(hexc(l.color2));
    }
    if (l.opacity < 0.999) sl.property("ADBE Transform Group").property("ADBE Opacity").setValue(100 * l.opacity);
    return sl;
  }
  function addText(c, l, idx) {
    var tl = c.layers.addText(l.text); tl.name = l.text.substr(0, 30);
    var sp = tl.property("ADBE Text Properties").property("ADBE Text Document"), td = sp.value;
    try { td.resetCharStyle(); td.resetParagraphStyle(); } catch (e) {}
    td.text = l.text;
    try { td.font = l.font; } catch (e) {}
    td.fontSize = l.size; td.applyFill = true; td.fillColor = hexc(l.color);
    if (l.stroke) { td.applyStroke = true; td.strokeColor = [0, 0, 0]; td.strokeWidth = l.stroke; td.strokeOverFill = false; } else td.applyStroke = false;
    td.tracking = Math.round(1000 * (l.letter_spacing || 0) / Math.max(1, l.size));
    td.justification = l.rtl ? (l.align === "center" ? ParagraphJustification.CENTER_JUSTIFY : ParagraphJustification.RIGHT_JUSTIFY)
                             : (l.align === "center" ? ParagraphJustification.CENTER_JUSTIFY : (l.align === "right" || l.align === "end" ? ParagraphJustification.RIGHT_JUSTIFY : ParagraphJustification.LEFT_JUSTIFY));
    if (l.rtl) { try { td.direction = ParagraphDirection.DIRECTION_RIGHT_TO_LEFT; } catch (e) {} try { td.composerEngine = ComposerEngine.UNIVERSAL_TYPE_ENGINE; } catch (e) {} }
    sp.setValue(td);
    var r = tl.sourceRectAtTime(0, false);
    tl.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([r.left + r.width / 2, r.top + r.height / 2]);
    tl.property("ADBE Transform Group").property("ADBE Position").setValue([l.box[0] + l.box[2] / 2, l.box[1] + l.box[3] / 2]);
    if (l.shadow) { var ds = tl.property("ADBE Effect Parade").addProperty("ADBE Drop Shadow"); ds.property(4).setValue(l.size * 0.06); ds.property(5).setValue(l.size * 0.25); }
    if (l.opacity < 0.999) tl.property("ADBE Transform Group").property("ADBE Opacity").setValue(100 * l.opacity);
    if (!td.font || td.font.toLowerCase().replace(/[^a-z]/g, "") !== l.font.toLowerCase().replace(/[^a-z]/g, "")) missingFonts[l.font] = l.family + " " + (l.style || "");
    return tl;
  }
  var missingFonts = {};
  // ── layers, bottom → top (AE adds new layers on top)
  var picLayers = {};
  for (var li = 0; li < DOC.video.length; li++) {
    var lane = DOC.video[li];
    if (lane.role === "gfx" || lane.role === "captions") continue;
    var prev = null;
    for (var k = 0; k < lane.items.length; k++) {
      var it = lane.items[k]; if (!it.mid) continue;
      var L = place(it, comp); if (!L) continue;
      setBox(L, media[it.mid], it); opacityKf(L, it);
      if (it.mask && it.mask.radius) roundedMask(L, media[it.mid], it.mask.radius, it.box);
      if (lane.role === "picture" || lane.role === "backdrop") transitionIn(L, prev, it);
      if (it.lut) L.comment = "Grade LUT: " + it.lut + "  (Effect › Color Correction › Lumetri Color › Creative › Look › Browse)";
      if (lane.role === "gfx_render") { L.name = it.name + " (rendered backup)"; if (it.enabled === false) { L.enabled = false; L.shy = true; } }
      if (it.has_audio_link) L.audioEnabled = false;
      else if (media[it.mid] && media[it.mid].hasAudio && lane.role !== "picture") L.audioEnabled = false;
      if (lane.role === "picture" && media[it.mid] && media[it.mid].hasAudio) L.audioEnabled = false;   // sound is on its own layers
      prev = L;
    }
  }
  // ── native graphics: one precomp per graphic
  for (var gi in DOC.graphics) {
    var g = DOC.graphics[gi], gc = proj.items.addComp("GFX · " + g.name, W, H, 1, Math.max(0.1, g.dur), FPS);
    gc.parentFolder = FG;
    for (var j = 0; j < g.layers.length; j++) {
      var ly = g.layers[j];
      if (ly.type === "rect") addShape(gc, ly, j);
      else if (ly.type === "text") addText(gc, ly, j);
      else if (ly.type === "image" && ly.mid && media[ly.mid]) { var il = gc.layers.add(media[ly.mid]); il.name = "Image " + j;
        setBox(il, media[ly.mid], {box: ly.box}); }
      // AE stacks new layers on top — keep the page order (later = on top)
    }
    g.comp = gc;
  }
  for (var lj = 0; lj < DOC.video.length; lj++) {
    var ln = DOC.video[lj];
    if (ln.role !== "gfx") continue;
    for (var q = 0; q < ln.items.length; q++) {
      var gi2 = ln.items[q], gg = DOC.graphics[gi2.gfx];
      var GL = comp.layers.add(gg.comp); GL.name = gg.name + " (editable)"; GL.startTime = gi2.rec_in; GL.inPoint = gi2.rec_in; GL.outPoint = gi2.rec_out;
      var GO = GL.property("ADBE Transform Group").property("ADBE Opacity"), GP = GL.property("ADBE Transform Group").property("ADBE Position");
      var ai = gg.anim["in"], ao = gg.anim.out, rise = gg.anim.rise, c = [W / 2, H / 2];
      GO.setValueAtTime(gi2.rec_in, 0); GO.setValueAtTime(gi2.rec_in + ai, 100); GO.setValueAtTime(gi2.rec_out - ao, 100); GO.setValueAtTime(gi2.rec_out, 0);
      GP.setValueAtTime(gi2.rec_in, [c[0], c[1] + rise]); GP.setValueAtTime(gi2.rec_in + ai, c);
      for (var kk = 1; kk <= GP.numKeys; kk++) { try { GP.setInterpolationTypeAtKey(kk, KeyframeInterpolationType.BEZIER); var ez = new KeyframeEase(0, 75); GP.setTemporalEaseAtKey(kk, [ez], [ez]); } catch (e) {} }
      if (MAKE_MOGRTS) { try { new Folder(ROOT.fsName + "/Projects/Premiere/MOGRT").create();
        for (var t2 = 1; t2 <= gg.comp.numLayers; t2++) { var lyr = gg.comp.layer(t2); if (lyr instanceof TextLayer) lyr.property("ADBE Text Properties").property("ADBE Text Document").addToMotionGraphicsTemplateAs(gg.comp, lyr.name); }
        gg.comp.exportAsMotionGraphicsTemplate(true, f("Projects/Premiere/MOGRT/" + gg.name.replace(/[^A-Za-z0-9؀-ۿ]+/g, "-") + ".mogrt").fsName);
      } catch (e) { missing.push("mogrt " + gg.name + ": " + e.toString()); } }
    }
  }
  // ── captions: live text layers in a precomp
  if (DOC.captions) {
    var cs = DOC.captions.spec, cc = proj.items.addComp("Captions", W, H, 1, DUR, FPS); cc.parentFolder = FC;
    for (var ci = 0; ci < DOC.captions.items.length; ci++) {
      var cp = DOC.captions.items[ci], rtl = /[؀-ۿ]/.test(cp.text), fnt = rtl ? cs.font_ar : cs.font;
      var txt = (cs.uppercase && !rtl) ? cp.text.toUpperCase() : cp.text;
      var lyr2 = addText(cc, {text: txt, font: fnt.ps, family: fnt.family, style: fnt.style, size: rtl ? cs.size_ar : cs.size,
                              color: cs.color, stroke: cs.outline * 2, align: "center", rtl: rtl, box: [0, 0, 0, 0], opacity: 1}, ci);
      var rr = lyr2.sourceRectAtTime(0, false);
      var y = cs.valign === "bottom" ? cs.anchor_y - rr.height / 2 : (cs.valign === "top" ? cs.anchor_y + rr.height / 2 : cs.anchor_y);
      lyr2.property("ADBE Transform Group").property("ADBE Position").setValue([W / 2, y]);
      lyr2.inPoint = cp.rec_in; lyr2.outPoint = cp.rec_out; lyr2.name = "Caption " + (ci + 1);
    }
    var CL = comp.layers.add(cc); CL.name = "Captions (editable)";
  }
  // ── sound
  for (var ai2 = 0; ai2 < DOC.audio.length; ai2++) {
    var al = DOC.audio[ai2];
    for (var a3 = 0; a3 < al.items.length; a3++) {
      var au = al.items[a3], AL = place(au, comp); if (!AL) continue;
      AL.name = al.name + " · " + au.name; try { AL.enabled = false; } catch (e) {}
      var lv = AL.property("ADBE Audio Group").property("ADBE Audio Levels"), span = au.rec_out - au.rec_in, base = au.gain_db || 0;
      var pts = []; if (au.kf) for (var z = 0; z < au.kf.length; z++) pts.push([au.kf[z][0], au.kf[z][1]]);
      if (au.fade_in) { pts.push([0, -48]); pts.push([Math.min(au.fade_in, span), base]); }
      if (au.fade_out) { pts.push([Math.max(0, span - au.fade_out), base]); pts.push([span, -48]); }
      if (pts.length) { pts.sort(function (p1, p2) { return p1[0] - p2[0]; }); for (var z2 = 0; z2 < pts.length; z2++) lv.setValueAtTime(au.rec_in + pts[z2][0], [pts[z2][1], pts[z2][1]]); }
      else if (Math.abs(base) > 0.01) lv.setValue([base, base]);
    }
  }
  // ── programme fades + markers
  if (DOC.fades && (DOC.fades["in"] || DOC.fades.out)) {
    var bk = comp.layers.addSolid([0, 0, 0], "Fade from/to black", W, H, 1, DUR), bo = bk.property("ADBE Transform Group").property("ADBE Opacity");
    if (DOC.fades["in"]) { bo.setValueAtTime(0, 100); bo.setValueAtTime(DOC.fades["in"], 0); } else bo.setValue(0);
    if (DOC.fades.out) { bo.setValueAtTime(DUR - DOC.fades.out, 0); bo.setValueAtTime(DUR, 100); }
  }
  for (var mk2 = 0; mk2 < (DOC.markers || []).length; mk2++) { var mv = new MarkerValue(DOC.markers[mk2].name || ""); comp.markerProperty.setValueAtTime(DOC.markers[mk2].t, mv); }
  comp.openInViewer();
  app.endUndoGroup();
  var saveTo = f("Projects/AfterEffects/" + DOC.file_name + ".aep");
  try { proj.save(saveTo); } catch (e) { missing.push("save: " + e.toString()); }
  var fl = []; for (var mf in missingFonts) fl.push(missingFonts[mf]);
  var msg = "Built '" + DOC.name + "' → " + saveTo.fsName;
  if (fl.length) msg += "\n\nInstall these fonts (in the package's Fonts folder), then reopen: " + fl.join(", ");
  if (missing.length) msg += "\n\nNot imported: " + missing.join(", ");
  if (!SILENT) alert(msg); else $.writeln(msg);
  var log = f("Projects/AfterEffects/build-log.txt"); log.encoding = "UTF-8"; log.open("w"); log.write(msg); log.close();
})();
"""


def write(d: dict, dest_dir: Path, root: Path, make_mogrts: bool = False) -> Path:
    """→ Projects/AfterEffects/build_project.jsx (data + builder in one file, ES3)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    data = {k: d[k] for k in ("name", "W", "H", "fps", "duration", "background", "fades", "markers", "graphics")}
    data["file_name"] = d["file_name"]
    data["media"] = {mid: {"rel": rel(m["path"], root), "kind": m["kind"], "alpha": m.get("alpha", False),
                           "generated": m.get("generated", False)} for mid, m in d["media"].items()}
    data["video"] = d["video"]
    data["audio"] = d["audio"]
    if d.get("captions"):
        lane = next((l for l in d["video"] if l["role"] == "captions"), {"items": []})
        data["captions"] = {"spec": d["captions"]["spec"], "items": lane["items"]}
    js = ("// After Effects project builder — generated by studio-mcp. Run: File › Scripts › Run Script File…\n"
          "// It imports the package's media, builds the comps and saves the .aep in this folder.\n"
          f"var MAKE_MOGRTS = {'true' if make_mogrts else 'false'};   // also export each graphic as a .mogrt for Premiere\n"
          "var SILENT = (typeof SILENT !== 'undefined') ? SILENT : false;\n"
          "var DOC = " + json.dumps(data, ensure_ascii=True, indent=None) + ";\n" + BUILDER)
    p = dest_dir / "build_project.jsx"
    p.write_text(js, encoding="utf-8")
    return p


def check(path: Path) -> list[str]:
    """Parse the script as ES3/ES5 with a real JS parser (node + acorn when available, else node --check)."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        return []
    js = "const fs=require('fs');try{new (require('vm').Script)(fs.readFileSync(process.argv[1],'utf8'),{filename:'build_project.jsx'})}catch(e){console.error(e.stack.split('\\n').slice(0,3).join(' | '));process.exit(1)}"
    r = subprocess.run([node, "-e", js, str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        return ["JSX syntax: " + r.stderr.strip()[:300]]
    src = path.read_text(encoding="utf-8")
    bad = [kw for kw in ("=>", "`", "let ", "const ") if kw in src.split("// ───────────────────────── builder")[-1]]
    if bad:
        return [f"JSX uses non-ES3 syntax: {bad}"]
    # dry-run the whole builder against a mock After Effects (catches runtime errors and missing media)
    import json as _j
    r = subprocess.run([node, str(Path(__file__).with_name("ae_mock.js")), str(path)], capture_output=True, text=True, timeout=120)
    try:
        out = _j.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return ["AE dry-run did not report: " + (r.stderr or r.stdout)[-200:]]
    if not out.get("ok"):
        return ["AE dry-run: " + out.get("error", "")[:300]]
    if "Not imported" in (out["stats"].get("alert") or ""):
        return ["AE dry-run: " + out["stats"]["alert"][-300:]]
    CHECK_STATS.update(out["stats"])
    return []


CHECK_STATS: dict = {}
