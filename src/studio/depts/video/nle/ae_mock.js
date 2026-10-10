// Runs build_project.jsx against a mock After Effects object model (node): catches script errors
// (undefined variables, wrong calls, missing media files) before the user opens AE.
// usage: node ae_mock.js /path/to/build_project.jsx   → prints JSON {ok, error, stats}
const fs = require('fs'), path = require('path'), vm = require('vm');
const script = process.argv[2];
const stats = {comps: 0, layers: 0, text: 0, shapes: 0, keys: 0, imports: 0, missing: [], saved: null, effects: 0, masks: 0};

function prop(name) {
  const p = {
    name, value: (/Position|Anchor|Scale|Center|Point/i.test(name) ? [0, 0] : 0), numKeys: 0, numProperties: 0,
    setValue(v) { this.value = v; }, setValueAtTime(t, v) { if (typeof t !== 'number' || isNaN(t)) throw new Error('bad time for ' + name); this.numKeys++; stats.keys++; },
    setInterpolationTypeAtKey() {}, setTemporalEaseAtKey() {},
    addToMotionGraphicsTemplateAs() { return true; },
    property(n) { return prop(String(n)); },
    addProperty(n) { if (/Mask/.test(n)) stats.masks++; else stats.effects++; return group(String(n)); },
  };
  return p;
}
function group(name) {
  const cache = {};
  const g = prop(name);
  g.property = (n) => (cache[n] = cache[n] || (/Group|Parade|Vectors|Text Properties/.test(String(n)) ? group(String(n)) : prop(String(n))));
  return g;
}
function textDocProp() {
  const p = prop('ADBE Text Document');
  p.value = {text: '', font: '', fontSize: 0, resetCharStyle() {}, resetParagraphStyle() {}};
  p.setValue = function (v) { this.value = v; };
  return p;
}
function layer(kind, src) {
  stats.layers++;
  const props = {};
  const L = {
    kind, source: src, name: '', enabled: true, shy: false, audioEnabled: true, startTime: 0, inPoint: 0, outPoint: 0, stretch: 100, parent: null, comment: '',
    property(n) {
      if (n === 'ADBE Text Properties') { return props[n] = props[n] || {property(m) { return props.td = props.td || textDocProp(); }}; }
      return props[n] = props[n] || group(String(n));
    },
    sourceRectAtTime() { return {left: -50, top: -20, width: 100, height: 40}; },
  };
  return L;
}
function comp(name, w, h, par, dur, fps) {
  if (!(w > 0 && h > 0 && dur > 0 && fps > 0)) throw new Error(`bad comp ${name} ${w}x${h} ${dur}s ${fps}`);
  stats.comps++;
  const layers = [];
  const c = {
    name, width: w, height: h, duration: dur, frameRate: fps, bgColor: [0, 0, 0], parentFolder: null, numLayers: 0,
    layers: {
      add(src) { if (!src) throw new Error('layers.add(undefined)'); const l = layer('av', src); layers.push(l); c.numLayers++; return l; },
      addText(t) { if (typeof t !== 'string') throw new Error('addText needs a string'); stats.text++; const l = layer('text', null); Object.setPrototypeOf(l, TextLayer.prototype); layers.push(l); c.numLayers++; return l; },
      addShape() { stats.shapes++; const l = layer('shape', null); layers.push(l); c.numLayers++; return l; },
      addSolid(col, n, w2, h2, p2, d2) { if (!(d2 > 0)) throw new Error('solid duration'); const l = layer('solid', null); layers.push(l); c.numLayers++; return l; },
      addNull() { const l = layer('null', null); layers.push(l); c.numLayers++; return l; },
    },
    layer(i) { return layers[i - 1]; },
    markerProperty: prop('marker'), openInViewer() {}, exportAsMotionGraphicsTemplate() { return true; },
    openInEssentialGraphics() {},
  };
  return c;
}
function TextLayer() {}
function SolidSource() {}
class File {
  constructor(p) { this.p = String(p); }
  get exists() { return fs.existsSync(this.p); }
  get fsName() { return path.resolve(this.p); }
  get parent() { return new File(path.dirname(path.resolve(this.p))); }
  open() { return true; } write(s) {} close() {}
}
class Folder extends File { create() { return true; } }
const items = [];
const project = {
  items: {
    addFolder(n) { const f = {name: n, parentFolder: null}; items.push(f); return f; },
    addComp(n, w, h, p, d, f) { const c = comp(n, w, h, p, d, f); items.push(c); return c; },
  },
  importFile(io) {
    if (!io.file.exists) throw new Error('missing file ' + io.file.fsName);
    stats.imports++;
    const isStill = /\.(png|jpe?g|webp|bmp|tiff?)$/i.test(io.file.p), isAudio = /\.(wav|mp3|m4a|aac|aiff?)$/i.test(io.file.p);
    return {name: path.basename(io.file.p), width: 1920, height: 1080, duration: isStill ? 0 : 10, hasVideo: !isAudio, hasAudio: !isStill,
            mainSource: {alphaMode: 0}, parentFolder: null};
  },
  save(f) { stats.saved = f.fsName; },
};
const sandbox = {
  app: {project, beginUndoGroup() {}, endUndoGroup() {}, newProject() { return project; }},
  $: {fileName: path.resolve(script), writeln() {}},
  alert(m) { stats.alert = String(m); }, File, Folder, ImportOptions: function (f) { this.file = f; this.sequence = false; },
  Shape: function () {}, MarkerValue: function (c) { this.comment = c; }, KeyframeEase: function () {}, TextLayer, SolidSource,
  AlphaMode: {STRAIGHT: 1}, ParagraphJustification: {LEFT_JUSTIFY: 0, CENTER_JUSTIFY: 1, RIGHT_JUSTIFY: 2},
  ParagraphDirection: {DIRECTION_RIGHT_TO_LEFT: 1}, ComposerEngine: {UNIVERSAL_TYPE_ENGINE: 1},
  KeyframeInterpolationType: {BEZIER: 1}, SILENT: true, Math, parseInt, parseFloat, String, Number, Object, Array, RegExp, isNaN,
};
try {
  vm.runInNewContext(fs.readFileSync(script, 'utf8'), sandbox, {filename: 'build_project.jsx'});
  console.log(JSON.stringify({ok: true, stats}));
} catch (e) {
  console.log(JSON.stringify({ok: false, error: String(e && e.stack || e).split('\n').slice(0, 3).join(' | '), stats}));
}
