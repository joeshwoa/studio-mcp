/* studio motion3d — deterministic three.js scenes for motion graphics.
 *
 * createScene(canvas, opts) → Promise<{render(t), duration}>. Everything is a pure function of the time t
 * (seconds): no state accumulates between frames, so the virtual clock can seek any frame, in any order,
 * in parallel pages. preserveDrawingBuffer keeps the frame for the screenshot; random numbers come from a
 * seeded generator.
 *
 * presets: title (extruded shaped text from SVG paths — correct Arabic because the shaping happens in
 * HarfBuzz before), logo (SVG → extruded, bevelled), product (.glb turntable), particles, abstract,
 * device (phone / laptop with an image or video on the screen).
 * camera: orbit | push-in | dolly | crane | static | turntable (object spins, camera still)
 * material: metal | chrome | gold | glass | plastic | matte | clay | neon | satin
 */
import * as THREE from 'three';
import {SVGLoader} from 'three/addons/loaders/SVGLoader.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
import {RoundedBoxGeometry} from 'three/addons/geometries/RoundedBoxGeometry.js';
import {EffectComposer} from 'three/addons/postprocessing/EffectComposer.js';
import {RenderPass} from 'three/addons/postprocessing/RenderPass.js';
import {UnrealBloomPass} from 'three/addons/postprocessing/UnrealBloomPass.js';
import {OutputPass} from 'three/addons/postprocessing/OutputPass.js';

const clamp01 = (x) => Math.max(0, Math.min(1, x));
const E = {
  outExpo: (x) => (x >= 1 ? 1 : 1 - Math.pow(2, -10 * x)),
  outCubic: (x) => 1 - Math.pow(1 - x, 3),
  inOutCubic: (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2),
  inOutSine: (x) => -(Math.cos(Math.PI * x) - 1) / 2,
  outBack: (x) => { const c1 = 1.4, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); },
  outQuart: (x) => 1 - Math.pow(1 - x, 4),
};
const prog = (t, a, d) => clamp01((t - a) / Math.max(1e-6, d));
function rng(seed) { let s = seed >>> 0; return () => { s = (s + 0x6D2B79F5) >>> 0; let t = s; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
const col = (c, d = '#ffffff') => new THREE.Color(c || d);

export function material(kind, color, opts = {}) {
  const c = col(color, '#ff5a36');
  switch (kind) {
    case 'chrome': return new THREE.MeshPhysicalMaterial({color: c.clone().lerp(col('#ffffff'), 0.55), metalness: 1, roughness: 0.06, envMapIntensity: 1.3});
    case 'gold': return new THREE.MeshPhysicalMaterial({color: col(opts.gold || '#E2B04A'), metalness: 1, roughness: 0.2, clearcoat: 0.4});
    case 'metal': return new THREE.MeshPhysicalMaterial({color: c, metalness: 0.85, roughness: 0.2, clearcoat: 0.6, clearcoatRoughness: 0.12, envMapIntensity: 1.35});
    case 'glass': return new THREE.MeshPhysicalMaterial({color: c.clone().lerp(col('#ffffff'), 0.65), metalness: 0, roughness: 0.04, transmission: 1,
      thickness: opts.thickness || 1.2, ior: 1.5, attenuationColor: c, attenuationDistance: 2.5, clearcoat: 1, specularIntensity: 1, envMapIntensity: 1.2});
    case 'matte': return new THREE.MeshStandardMaterial({color: c, metalness: 0, roughness: 0.85});
    case 'clay': return new THREE.MeshStandardMaterial({color: c.clone().lerp(col('#f2ede4'), 0.35), metalness: 0, roughness: 0.95});
    case 'neon': return new THREE.MeshStandardMaterial({color: c, emissive: c, emissiveIntensity: 2.2, roughness: 0.4});
    case 'satin': return new THREE.MeshPhysicalMaterial({color: c, metalness: 0.3, roughness: 0.45, sheen: 1, sheenColor: c.clone().lerp(col('#ffffff'), 0.5)});
    case 'plastic': default: return new THREE.MeshPhysicalMaterial({color: c, metalness: 0, roughness: 0.32, clearcoat: 1, clearcoatRoughness: 0.12});
  }
}
function sideMaterial(kind, color) {
  if (['chrome', 'gold', 'glass', 'neon'].includes(kind)) return material(kind, color);
  return material(kind === 'matte' || kind === 'clay' ? kind : 'metal', col(color).multiplyScalar(0.45).getStyle());
}

/** SVG markup → THREE.Group of extruded shapes, centred, scaled so its larger side = size world units;
 *  depth / bevel are in world units too. */
function extrudeSVG(svgText, o) {
  const data = new SVGLoader().parse(svgText);
  const items = [];
  const box = new THREE.Box2();
  data.paths.forEach((p, i) => {
    const style = p.userData?.style || {};
    const fill = style.fill && style.fill !== 'none' ? style.fill : null;
    if (!fill) return;
    const shapes = p.toShapes();
    for (const sh of shapes) for (const v of sh.getPoints(12)) box.expandByPoint(v);
    items.push({shapes, color: fill !== 'currentColor' ? p.color.getStyle() : '#ffffff', i});
  });
  if (!items.length) throw new Error('motion3d: the SVG has no filled shapes');
  const sz = box.getSize(new THREE.Vector2()), ctr = box.getCenter(new THREE.Vector2());
  const s = (o.size || 4) / Math.max(sz.x, sz.y, 1e-6);
  const depth = (o.depth ?? 0.25) / s, bevel = (o.bevel ?? 0.03) / s;
  const inner = new THREE.Group();
  const mcache = {};
  for (const it of items) {
    const face = o.color || it.color, side = o.side || face;
    const key = face + '|' + side;
    const mats = mcache[key] || (mcache[key] = [material(o.material, face, o), sideMaterial(o.material, side)]);
    for (const sh of it.shapes) {
      const geo = new THREE.ExtrudeGeometry(sh, {depth, bevelEnabled: bevel > 0, bevelThickness: bevel, bevelSize: bevel * 0.55,
        bevelOffset: 0, bevelSegments: 5, curveSegments: 16});
      geo.translate(-ctr.x, -ctr.y, -depth / 2);
      const m = new THREE.Mesh(geo, mats); m.castShadow = true; m.receiveShadow = true; m.userData.order = it.i;
      inner.add(m);
    }
  }
  inner.scale.set(s, -s, s);  // SVG is y-down
  const wrap = new THREE.Group(); wrap.add(inner);
  return wrap;
}

function studioLights(scene, renderer, o, R) {
  const pm = new THREE.PMREMGenerator(renderer);
  scene.environment = pm.fromScene(new RoomEnvironment(), 0.035).texture;
  scene.environmentIntensity = o.env ?? 0.9;
  const key = new THREE.DirectionalLight(0xffffff, o.key ?? 2.2);
  key.position.set(-4, 6, 6); key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024); key.shadow.radius = 6; key.shadow.bias = -0.0005;
  Object.assign(key.shadow.camera, {left: -8, right: 8, top: 8, bottom: -8, near: 0.5, far: 30});
  scene.add(key);
  const rim = new THREE.DirectionalLight(col(o.rim || o.accent || '#88aaff'), o.rimIntensity ?? 2.5);
  rim.position.set(5, 2, -6); scene.add(rim);
  const fill = new THREE.HemisphereLight(0xffffff, col(o.ground || '#222233'), 0.35); scene.add(fill);
  return {key, rim};
}

function addGround(scene, y, alpha) {
  const g = new THREE.Mesh(new THREE.PlaneGeometry(60, 60), new THREE.ShadowMaterial({opacity: alpha ? 0.28 : 0.35}));
  g.rotation.x = -Math.PI / 2; g.position.y = y; g.receiveShadow = true; scene.add(g); return g;
}

function fitDistance(size, fov, aspect, fill = 0.72) {
  const vf = THREE.MathUtils.degToRad(fov);
  const hDist = (size.y / fill) / 2 / Math.tan(vf / 2);
  const wDist = (size.x / fill) / 2 / Math.tan(vf / 2) / aspect;
  return Math.max(hDist, wDist) + size.z / 2;
}

function screenTexture(o) {
  if (o.screen_video) {
    const v = document.createElement('video');
    v.src = o.screen_video; v.muted = true; v.playsInline = true; v.preload = 'auto'; v.crossOrigin = 'anonymous';
    v.dataset.start = String(o.start || 0); v.dataset.loop = 'true';
    v.style.cssText = 'position:absolute;width:2px;height:2px;opacity:0;pointer-events:none';
    document.body.appendChild(v);
    const tex = new THREE.VideoTexture(v); tex.colorSpace = THREE.SRGBColorSpace;
    tex.__video = v;
    return {tex, ready: new Promise(r => { v.addEventListener('loadeddata', r, {once: true}); setTimeout(r, 8000); })};
  }
  if (o.screen_image) {
    const tex = new THREE.TextureLoader().load(o.screen_image);
    tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 8;
    return {tex, ready: new Promise(r => { const im = new Image(); im.onload = r; im.onerror = r; im.src = o.screen_image; })};
  }
  // generated placeholder UI so the mockup never looks empty
  const cv = document.createElement('canvas'); cv.width = 590; cv.height = 1280;
  const g = cv.getContext('2d');
  const gr = g.createLinearGradient(0, 0, 0, 1280); gr.addColorStop(0, o.accent || '#2b2f77'); gr.addColorStop(1, o.color || '#ff5a36');
  g.fillStyle = gr; g.fillRect(0, 0, 590, 1280);
  g.fillStyle = 'rgba(255,255,255,.9)'; g.fillRect(48, 140, 300, 34); g.fillStyle = 'rgba(255,255,255,.5)'; g.fillRect(48, 196, 420, 20);
  for (let i = 0; i < 4; i++) { g.fillStyle = 'rgba(255,255,255,.16)'; g.beginPath(); g.roundRect(40, 280 + i * 220, 510, 190, 26); g.fill(); }
  const tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace;
  return {tex, ready: Promise.resolve()};
}

function phone(o) {
  const grp = new THREE.Group();
  const W = 1.0, H = 2.05, D = 0.11;
  const body = new THREE.Mesh(new RoundedBoxGeometry(W, H, D, 8, 0.12), material(o.body_material || 'metal', o.body_color || '#1b1d24'));
  body.castShadow = true; grp.add(body);
  const st = screenTexture(o);
  const scr = new THREE.Mesh(new THREE.PlaneGeometry(W * 0.92, H * 0.955), new THREE.MeshBasicMaterial({map: st.tex, toneMapped: false}));
  // rounded screen corners via alpha map
  const am = document.createElement('canvas'); am.width = 256; am.height = 530; const ag = am.getContext('2d');
  ag.fillStyle = '#000'; ag.fillRect(0, 0, 256, 530); ag.fillStyle = '#fff'; ag.beginPath(); ag.roundRect(0, 0, 256, 530, 26); ag.fill();
  scr.material.alphaMap = new THREE.CanvasTexture(am); scr.material.transparent = true;
  scr.position.z = D / 2 + 0.002; grp.add(scr);
  const isl = new THREE.Mesh(new RoundedBoxGeometry(0.28, 0.075, 0.01, 4, 0.035), new THREE.MeshBasicMaterial({color: 0x000000}));
  isl.position.set(0, H * 0.455, D / 2 + 0.006); grp.add(isl);
  const glass = new THREE.Mesh(new THREE.PlaneGeometry(W * 0.92, H * 0.955), new THREE.MeshPhysicalMaterial({
    color: 0xffffff, metalness: 0, roughness: 0.02, transparent: true, opacity: 0.08, clearcoat: 1}));
  glass.position.z = D / 2 + 0.008; grp.add(glass);
  grp.userData.screen = st;
  return grp;
}

function laptop(o) {
  const grp = new THREE.Group();
  const bodyMat = material(o.body_material || 'metal', o.body_color || '#c9ccd3');
  const base = new THREE.Mesh(new RoundedBoxGeometry(3.2, 0.09, 2.2, 6, 0.04), bodyMat); base.castShadow = true;
  const lid = new THREE.Group();
  const lidBox = new THREE.Mesh(new RoundedBoxGeometry(3.2, 2.1, 0.06, 6, 0.03), bodyMat); lidBox.position.y = 1.05; lid.add(lidBox);
  const st = screenTexture(Object.assign({}, o));
  const scr = new THREE.Mesh(new THREE.PlaneGeometry(3.0, 1.88), new THREE.MeshBasicMaterial({map: st.tex, toneMapped: false}));
  scr.position.set(0, 1.07, 0.032); lid.add(scr);
  lid.position.set(0, 0.045, -1.08); lid.rotation.x = -0.22;
  grp.add(base); grp.add(lid); grp.userData.screen = st; grp.userData.lid = lid;
  return grp;
}

export async function createScene(canvas, o) {
  o = Object.assign({preset: 'title', camera: 'orbit', material: 'plastic', color: '#FF5A36', accent: '#FFC53D', duration: 6,
                     fov: 30, seed: 7, transparent: false, bloom: 0, shadow: true}, o || {});
  const Wd = canvas.width, Ht = canvas.height, aspect = Wd / Ht;
  const R = rng(o.seed);
  const renderer = new THREE.WebGLRenderer({canvas, antialias: true, alpha: true, preserveDrawingBuffer: true, powerPreference: 'high-performance'});
  renderer.setPixelRatio(1); renderer.setSize(Wd, Ht, false);
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = o.exposure ?? 1.0;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.shadowMap.enabled = !!o.shadow; renderer.shadowMap.type = THREE.PCFShadowMap;
  renderer.setClearColor(0x000000, 0);
  const scene = new THREE.Scene();
  const cam = new THREE.PerspectiveCamera(o.fov, aspect, 0.05, 200);
  studioLights(scene, renderer, o, R);
  const T = o.duration;
  const root = new THREE.Group(); scene.add(root);
  let subject = null, extra = {};
  const waits = [];

  if (o.preset === 'title' || o.preset === 'logo') {
    if (!o.svg) throw new Error('motion3d: no SVG for ' + o.preset);
    subject = extrudeSVG(o.svg, {material: o.material, color: o.preset === 'title' ? o.color : o.face_color, side: o.side_color,
                                 depth: o.depth ?? (o.preset === 'title' ? 0.35 : 0.3), bevel: o.bevel ?? 0.025, size: 4});
    root.add(subject);
  } else if (o.preset === 'product') {
    if (!o.glb) throw new Error('motion3d: product needs a .glb');
    const gltf = await new GLTFLoader().parseAsync(await (await fetch(o.glb)).arrayBuffer(), '');
    subject = new THREE.Group(); subject.add(gltf.scene);
    gltf.scene.traverse(m => { if (m.isMesh) { m.castShadow = true; m.receiveShadow = true; } });
    const b = new THREE.Box3().setFromObject(gltf.scene), s = b.getSize(new THREE.Vector3()), c = b.getCenter(new THREE.Vector3());
    gltf.scene.position.sub(c); subject.scale.setScalar(3 / Math.max(s.x, s.y, s.z, 1e-6));
    root.add(subject);
  } else if (o.preset === 'device') {
    subject = (o.device === 'laptop' ? laptop : phone)(o);
    waits.push(subject.userData.screen.ready);
    root.add(subject);
  } else if (o.preset === 'particles') {
    const n = o.count || 2600;
    const pos = new Float32Array(n * 3), colArr = new Float32Array(n * 3), seeds = new Float32Array(n);
    const pal = [col(o.color), col(o.accent), col('#ffffff')];
    for (let i = 0; i < n; i++) {
      pos[i * 3] = (R() - 0.5) * 22; pos[i * 3 + 1] = (R() - 0.5) * 13; pos[i * 3 + 2] = -R() * 18 + 3;
      const c = pal[i % 7 === 0 ? 2 : (R() < 0.6 ? 0 : 1)]; colArr.set([c.r, c.g, c.b], i * 3); seeds[i] = R() * 100;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos.slice(), 3)); geo.setAttribute('color', new THREE.BufferAttribute(colArr, 3));
    const spr = document.createElement('canvas'); spr.width = spr.height = 64; const sg = spr.getContext('2d');
    const rg = sg.createRadialGradient(32, 32, 0, 32, 32, 32); rg.addColorStop(0, 'rgba(255,255,255,1)'); rg.addColorStop(0.35, 'rgba(255,255,255,.55)'); rg.addColorStop(1, 'rgba(255,255,255,0)');
    sg.fillStyle = rg; sg.fillRect(0, 0, 64, 64);
    const pts = new THREE.Points(geo, new THREE.PointsMaterial({size: o.point_size || 0.09, map: new THREE.CanvasTexture(spr), vertexColors: true,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, sizeAttenuation: true}));
    root.add(pts); extra = {pts, base: pos, seeds};
    scene.fog = new THREE.FogExp2(0x000000, 0.035);
  } else if (o.preset === 'abstract') {
    const shapes = [];
    const geos = [new THREE.TorusGeometry(0.9, 0.32, 48, 120), new THREE.SphereGeometry(0.75, 64, 48), new RoundedBoxGeometry(1.2, 1.2, 1.2, 6, 0.22),
                  new THREE.TorusKnotGeometry(0.6, 0.2, 180, 32), new THREE.CapsuleGeometry(0.4, 1.0, 12, 32), new THREE.IcosahedronGeometry(0.8, 0)];
    const kinds = [o.material, 'glass', 'chrome', o.material, 'satin', o.material];
    const cnt = o.count || 7;
    for (let i = 0; i < cnt; i++) {
      const m = new THREE.Mesh(geos[i % geos.length], material(kinds[i % kinds.length], i % 2 ? o.accent : o.color));
      const a = (i / cnt) * Math.PI * 2;
      m.userData = {r: 2.2 + R() * 1.6, a, y: (R() - 0.5) * 2.4, z: -R() * 2.5, spin: 0.2 + R() * 0.5, ph: R() * 6.28};
      m.castShadow = true; root.add(m); shapes.push(m);
    }
    extra.shapes = shapes;
  }

  // ground shadow under solid subjects
  let size = new THREE.Vector3(4, 2, 1);
  if (subject) {
    const b = new THREE.Box3().setFromObject(subject); size = b.getSize(new THREE.Vector3());
    if (o.shadow && o.preset !== 'title' && o.preset !== 'logo') addGround(scene, b.min.y - 0.02, o.transparent);
    else if (o.shadow) addGround(scene, b.min.y - size.y * 0.55, o.transparent);
  }
  const fill = o.fill ?? (o.preset === 'device' ? 0.62 : 0.7);
  const dist = subject ? fitDistance(size, o.fov, aspect, fill) : 9;

  let composer = null;
  if (o.bloom > 0 && !o.transparent) {
    composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, cam));
    composer.addPass(new UnrealBloomPass(new THREE.Vector2(Wd, Ht), o.bloom, 0.6, 0.82));
    composer.addPass(new OutputPass());
  }
  await Promise.all(waits);

  function placeCamera(t) {
    const p = clamp01(t / T), e = E.inOutSine(p), mode = o.camera;
    let az = 0, el = 0.12, d = dist, ty = 0;
    if (mode === 'orbit') { az = THREE.MathUtils.degToRad((o.orbit_degrees ?? 50)) * (e - 0.5); el = 0.14 + 0.06 * Math.sin(p * Math.PI); }
    else if (mode === 'push-in') { d = dist * (1.22 - 0.26 * E.outCubic(p)); az = -0.12 + 0.12 * e; }
    else if (mode === 'dolly') { d = dist * (0.92 + 0.2 * e); az = 0.35 - 0.7 * e; }
    else if (mode === 'crane') { el = 0.55 - 0.45 * e; d = dist * (1.1 - 0.1 * e); }
    else if (mode === 'static' || mode === 'turntable') { az = mode === 'static' ? 0 : 0; el = 0.1; }
    if (o.preset === 'particles') { cam.position.set(Math.sin(p * 2) * 0.6, Math.cos(p * 1.3) * 0.3, 6 - 7 * e); cam.lookAt(0, 0, -12); return; }
    if (o.preset === 'device' || o.preset === 'product') el += 0.06;
    cam.position.set(Math.sin(az) * Math.cos(el) * d, Math.sin(el) * d + ty, Math.cos(az) * Math.cos(el) * d);
    cam.lookAt(0, 0, 0);
  }

  function animateSubject(t) {
    if (!subject) return;
    const anim = o.animation || (o.preset === 'title' ? 'rise' : o.preset === 'logo' ? 'spin-in' : o.preset === 'device' ? 'float' : 'turntable');
    subject.position.set(0, 0, 0); subject.rotation.set(0, 0, 0); subject.scale.setScalar(1); subject.visible = true;
    const inD = Math.min(1.6, T * 0.35);
    const pi = prog(t, 0.1, inD);
    if (anim === 'rise') {
      subject.position.y = -2.4 * (1 - E.outExpo(pi)); subject.rotation.x = 1.1 * (1 - E.outExpo(pi));
      subject.rotation.y = 0.06 * Math.sin(t * 0.8);
      subject.visible = t > 0.1;
    } else if (anim === 'spin-in') {
      subject.rotation.y = -Math.PI * 1.5 * (1 - E.outQuart(pi)) + 0.12 * Math.sin(t * 0.9) * pi;
      subject.scale.setScalar(0.4 + 0.6 * E.outBack(pi));
    } else if (anim === 'flip') {
      subject.rotation.x = -Math.PI * (1 - E.outBack(pi));
    } else if (anim === 'drop') {
      const b = prog(t, 0.1, inD); const y = 3.5 * Math.pow(1 - b, 2) * Math.abs(Math.cos(b * Math.PI * 2.5));
      subject.position.y = y;
    } else if (anim === 'zoom-through') {
      subject.position.z = -14 * (1 - E.outExpo(pi)); subject.rotation.y = 0.5 * (1 - E.outExpo(pi));
    } else if (anim === 'turntable') {
      subject.rotation.y = (o.spin ?? 1) * Math.PI * 2 * (t / T) + (o.start_angle || -0.5);
    } else if (anim === 'float') {
      subject.rotation.y = -0.35 + 0.5 * E.inOutSine(clamp01(t / T)); subject.position.y = 0.06 * Math.sin(t * 1.6);
      subject.rotation.x = -0.05 + 0.03 * Math.sin(t * 1.1);
      if (pi < 1) { subject.position.y -= 1.5 * (1 - E.outExpo(pi)); subject.rotation.y -= 0.8 * (1 - E.outExpo(pi)); }
    }
    const outD = o.fade_out ? 0.6 : 0;
    if (outD) subject.scale.multiplyScalar(1 - 0.15 * E.inOutCubic(prog(t, T - outD, outD)));
  }

  function animateExtra(t) {
    if (extra.pts) {
      const a = extra.pts.geometry.attributes.position, b = extra.base, s = extra.seeds;
      for (let i = 0; i < s.length; i++) {
        a.array[i * 3] = b[i * 3] + Math.sin(t * 0.3 + s[i]) * 0.35;
        a.array[i * 3 + 1] = b[i * 3 + 1] + Math.cos(t * 0.25 + s[i] * 1.3) * 0.3 + t * 0.05;
        a.array[i * 3 + 2] = b[i * 3 + 2];
      }
      a.needsUpdate = true;
    }
    if (extra.shapes) {
      for (const m of extra.shapes) {
        const u = m.userData, a = u.a + t * 0.18;
        m.position.set(Math.cos(a) * u.r, u.y + 0.25 * Math.sin(t * 0.9 + u.ph), Math.sin(a) * u.r * 0.6 + u.z);
        m.rotation.set(t * u.spin + u.ph, t * u.spin * 0.7, 0);
        const intro = E.outBack(prog(t, 0.1 + (u.ph / 6.28) * 0.6, 1.0)); m.scale.setScalar(Math.max(0.0001, intro));
      }
    }
  }

  function render(t) {
    // slowly turning studio environment → reflections glide across metal/glass (the "premium" glint)
    if (scene.environmentRotation) scene.environmentRotation.y = (o.env_spin ?? 0.35) * t - 0.6;
    animateSubject(t); animateExtra(t); placeCamera(t);
    const st = subject?.userData?.screen; if (st?.tex?.__video) st.tex.needsUpdate = true;
    if (composer) composer.render(); else renderer.render(scene, cam);
  }
  render(0);
  return {render, duration: T, renderer};
}
