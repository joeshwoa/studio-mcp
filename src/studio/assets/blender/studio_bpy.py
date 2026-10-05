"""studio-mcp Blender job runner (Blender 4.x; 3.6+ tolerated).

    blender -b --factory-startup -P studio_bpy.py -- job.json

job.json = {
  "preset": "title" | "logo" | "turntable" | "abstract",
  "svg": "/path/text-or-logo.svg",          # title / logo (text is pre-shaped to outlines → correct Arabic)
  "glb": "/path/model.glb",                 # turntable
  "frames_dir": "/tmp/x/frames",            # PNG sequence f_000001.png …
  "blend_path": "/out/master.blend",        # editable master saved before rendering
  "width": 1920, "height": 1080, "fps": 30, "duration": 5.0,
  "engine": "EEVEE" | "CYCLES", "samples": 64, "transparent": false,
  "material": "metal|chrome|gold|glass|plastic|matte|clay|neon|satin",
  "color": "#FF5A36", "accent": "#FFC53D", "background": "#0E0F1A",
  "camera": "orbit|push-in|dolly|static|turntable", "animation": "rise|spin-in|flip|turntable|float",
  "depth": 0.35, "bevel": 0.03, "dof": true, "motion_blur": true, "seed": 7, "render": true
}
Writes progress lines "STUDIO: …" on stdout; exits non-zero on failure.
"""
import json
import math
import os
import random
import sys

try:
    import bpy  # noqa: F401  (only inside Blender)
    from mathutils import Vector
except ImportError:  # imported by tests / tools outside Blender
    bpy = None
    Vector = None


def log(msg):
    print("STUDIO: " + str(msg), flush=True)


def parse_args(argv):
    """Everything after '--' is ours: the job file path."""
    if "--" not in argv:
        raise SystemExit("usage: blender -b -P studio_bpy.py -- job.json")
    rest = argv[argv.index("--") + 1:]
    if not rest:
        raise SystemExit("missing job.json")
    with open(rest[0], "r", encoding="utf-8") as f:
        job = json.load(f)
    job.setdefault("preset", "title")
    job.setdefault("width", 1920)
    job.setdefault("height", 1080)
    job.setdefault("fps", 30)
    job.setdefault("duration", 5.0)
    job.setdefault("engine", "EEVEE")
    job.setdefault("samples", 64)
    job.setdefault("material", "metal")
    job.setdefault("color", "#FF5A36")
    job.setdefault("accent", "#FFC53D")
    job.setdefault("background", "#0E0F1A")
    job.setdefault("camera", "push-in")
    job.setdefault("depth", 0.35)
    job.setdefault("bevel", 0.03)
    job.setdefault("dof", True)
    job.setdefault("motion_blur", True)
    job.setdefault("seed", 7)
    job.setdefault("render", True)
    return job


def hex_rgba(h, a=1.0):
    """'#RRGGBB' → linear RGBA tuple (Blender colour inputs are scene-linear)."""
    h = str(h).lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    srgb = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb]
    return (lin[0], lin[1], lin[2], a)


def set_input(node, names, value):
    """Principled BSDF socket names changed across versions (e.g. 'Transmission' → 'Transmission Weight')."""
    for n in names if isinstance(names, (list, tuple)) else [names]:
        if n in node.inputs:
            try:
                node.inputs[n].default_value = value
                return True
            except Exception:
                pass
    return False


# ------------------------------------------------------------------------------------------- scene
def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def setup_render(job):
    sc = bpy.context.scene
    r = sc.render
    r.resolution_x, r.resolution_y, r.resolution_percentage = int(job["width"]), int(job["height"]), 100
    fps = float(job["fps"])
    r.fps = int(round(fps))
    r.fps_base = r.fps / fps if fps else 1.0
    sc.frame_start = 1
    sc.frame_end = max(1, int(round(float(job["duration"]) * fps)))
    eng = str(job["engine"]).upper()
    if eng == "CYCLES":
        r.engine = "CYCLES"
        cy = sc.cycles
        cy.device = "CPU"
        cy.samples = int(job["samples"])
        cy.use_adaptive_sampling = True
        try:
            cy.use_denoising = True
            cy.denoiser = "OPENIMAGEDENOISE"
        except Exception:
            pass
        cy.seed = int(job["seed"])
        try:
            cy.max_bounces, cy.transmission_bounces, cy.glossy_bounces = 12, 12, 6
            cy.caustics_refractive, cy.caustics_reflective = True, True
        except Exception:
            pass
    else:
        for name in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):  # 4.2–4.x use EEVEE Next; 4.1 and 5.x 'BLENDER_EEVEE'
            try:
                r.engine = name
                break
            except TypeError:
                continue
        ee = sc.eevee
        for attr, val in (("taa_render_samples", int(job["samples"])), ("use_gtao", True), ("use_bloom", True),
                          ("use_ssr", True), ("use_ssr_refraction", True), ("use_soft_shadows", True),
                          ("use_shadows", True), ("use_raytracing", True)):
            try:
                setattr(ee, attr, val)
            except Exception:
                pass
    r.film_transparent = bool(job.get("transparent"))
    r.use_motion_blur = bool(job.get("motion_blur"))
    try:
        r.motion_blur_shutter = 0.5
    except Exception:
        pass
    r.image_settings.file_format = "PNG"
    r.image_settings.color_mode = "RGBA"
    r.image_settings.color_depth = "8"
    os.makedirs(job["frames_dir"], exist_ok=True)
    r.filepath = os.path.join(job["frames_dir"], "f_######")
    r.use_file_extension = True
    vs = sc.view_settings
    for vt, look in (("AgX", "AgX - Medium High Contrast"), ("Filmic", "Medium High Contrast")):
        try:
            vs.view_transform = vt
            try:
                vs.look = look
            except Exception:
                pass
            break
        except Exception:
            continue


def world(job):
    w = bpy.data.worlds.new("Studio")
    bpy.context.scene.world = w
    w.use_nodes = True
    nt = w.node_tree
    bg = nt.nodes.get("Background")
    bg.inputs["Color"].default_value = hex_rgba(job["background"])
    bg.inputs["Strength"].default_value = 0.6


def area_light(name, loc, rot, size, power, color="#FFFFFF"):
    d = bpy.data.lights.new(name, type="AREA")
    d.size = size
    d.energy = power
    d.color = hex_rgba(color)[:3]
    o = bpy.data.objects.new(name, d)
    bpy.context.collection.objects.link(o)
    o.location = loc
    o.rotation_euler = rot
    return o


def studio_lights(job):
    area_light("Key", (-4.0, -5.0, 5.0), (math.radians(55), 0, math.radians(-38)), 5.0, 1500)
    area_light("Rim", (5.0, 4.0, 3.0), (math.radians(-60), 0, math.radians(130)), 4.0, 1800, job["accent"])
    area_light("Fill", (5.0, -6.0, 1.5), (math.radians(80), 0, math.radians(40)), 8.0, 350)
    area_light("Top", (0.0, 0.0, 7.0), (0, 0, 0), 6.0, 600)


def backdrop(job, z):
    """Curved cyclorama: floor sweeping up into a wall — the classic product/title studio."""
    bpy.ops.mesh.primitive_plane_add(size=40, location=(0, 6, z))
    floor = bpy.context.active_object
    floor.name = "Cyclorama"
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.subdivide(number_cuts=20)
    bpy.ops.object.mode_set(mode="OBJECT")
    for v in floor.data.vertices:  # bend the back half upward
        if v.co.y > 0:
            v.co.z += (v.co.y ** 2) * 0.12
    bpy.ops.object.shade_smooth()
    m = bpy.data.materials.new("Backdrop")
    m.use_nodes = True
    b = m.node_tree.nodes.get("Principled BSDF")
    set_input(b, "Base Color", hex_rgba(job["background"]))
    set_input(b, "Roughness", 0.55)
    floor.data.materials.append(m)
    if job.get("transparent"):
        try:
            floor.is_shadow_catcher = True  # Cycles: only the shadow is kept over transparency
        except Exception:
            floor.hide_render = True
    return floor


def material(kind, color, name="Mat"):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes.get("Principled BSDF")
    c = hex_rgba(color)
    set_input(b, "Base Color", c)
    if kind in ("metal", "chrome", "gold"):
        if kind == "gold":
            set_input(b, "Base Color", hex_rgba("#E2B04A"))
        if kind == "chrome":
            set_input(b, "Base Color", (0.85, 0.85, 0.88, 1))
        set_input(b, "Metallic", 1.0)
        set_input(b, "Roughness", {"metal": 0.22, "chrome": 0.04, "gold": 0.18}[kind])
        set_input(b, ["Coat Weight", "Clearcoat"], 0.4)
    elif kind == "glass":
        set_input(b, ["Transmission Weight", "Transmission"], 1.0)
        set_input(b, "Roughness", 0.02)
        set_input(b, "IOR", 1.45)
        set_input(b, "Base Color", tuple(0.6 + 0.4 * x for x in c[:3]) + (1,))
        try:
            m.blend_method = "HASHED"  # EEVEE (legacy) refraction preview
            m.use_screen_refraction = True
        except Exception:
            pass
    elif kind == "neon":
        set_input(b, ["Emission Color", "Emission"], c)
        set_input(b, "Emission Strength", 6.0)
    elif kind in ("matte", "clay"):
        set_input(b, "Roughness", 0.9)
    elif kind == "satin":
        set_input(b, "Roughness", 0.4)
        set_input(b, ["Sheen Weight", "Sheen"], 1.0)
    else:  # plastic
        set_input(b, "Roughness", 0.3)
        set_input(b, ["Coat Weight", "Clearcoat"], 1.0)
        set_input(b, ["Coat Roughness", "Clearcoat Roughness"], 0.08)
    return m


def import_svg_extruded(job):
    """SVG → curves (Blender's SVG importer) → one object, normalised to 4 m wide, extruded + bevelled."""
    try:
        import addon_utils
        addon_utils.enable("io_curve_svg", default_set=True)
    except Exception:
        pass
    before = set(bpy.data.objects)
    bpy.ops.import_curve.svg(filepath=job["svg"])
    objs = [o for o in bpy.data.objects if o not in before and o.type == "CURVE"]
    if not objs:
        raise RuntimeError("the SVG produced no curves")
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    if len(objs) > 1:
        bpy.ops.object.join()
    ob = bpy.context.view_layer.objects.active
    ob.name = "Subject"
    bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
    ob.location = (0, 0, 0)
    bpy.context.view_layer.update()
    dims = ob.dimensions
    s = 4.0 / max(dims.x, dims.y, 1e-6)
    # extrude/bevel are set in the curve's own units, then the object is scaled (applying a scale to an
    # imported SVG curve does not rescale its extrusion consistently across Blender versions)
    cu = ob.data
    cu.dimensions = "2D"
    cu.fill_mode = "BOTH"
    cu.extrude = float(job["depth"]) / 2.0 / s
    cu.bevel_depth = float(job["bevel"]) / s
    cu.bevel_resolution = 4
    cu.resolution_u = 24
    ob.scale = (s, s, s)
    ob.rotation_euler = (math.radians(90), 0, 0)  # stand the text up, facing −Y (towards the camera)
    ob.data.materials.clear()
    ob.data.materials.append(material(job["material"], job["color"], "Face"))
    return ob


def import_glb(job):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=job["glb"])
    new = [o for o in bpy.data.objects if o not in before]
    pivot = bpy.data.objects.new("Subject", None)
    bpy.context.collection.objects.link(pivot)
    for o in new:
        if o.parent is None:
            o.parent = pivot
    bpy.context.view_layer.update()
    mn = Vector((1e9, 1e9, 1e9))
    mx = Vector((-1e9, -1e9, -1e9))
    for o in new:
        if o.type == "MESH":
            for c in o.bound_box:
                w = o.matrix_world @ Vector(c)
                mn = Vector(map(min, mn, w))
                mx = Vector(map(max, mx, w))
    size = max((mx - mn).x, (mx - mn).y, (mx - mn).z, 1e-6)
    s = 3.0 / size
    pivot.scale = (s, s, s)
    ctr = (mn + mx) / 2
    for o in new:
        if o.parent is pivot:
            o.location -= Vector((ctr.x, ctr.y, mn.z))
    return pivot


def abstract_shapes(job):
    rnd = random.Random(int(job["seed"]))
    parent = bpy.data.objects.new("Subject", None)
    bpy.context.collection.objects.link(parent)
    kinds = [job["material"], "glass", "chrome", job["material"], "satin", "glass", job["material"]]
    adders = [lambda: bpy.ops.mesh.primitive_torus_add(major_radius=0.9, minor_radius=0.32, major_segments=96, minor_segments=32),
              lambda: bpy.ops.mesh.primitive_uv_sphere_add(radius=0.75, segments=64, ring_count=32),
              lambda: bpy.ops.mesh.primitive_cube_add(size=1.2),
              lambda: bpy.ops.mesh.primitive_ico_sphere_add(radius=0.8, subdivisions=1),
              lambda: bpy.ops.mesh.primitive_cylinder_add(radius=0.45, depth=1.4, vertices=64)]
    T = int(bpy.context.scene.frame_end)
    for i in range(int(job.get("count", 7))):
        adders[i % len(adders)]()
        o = bpy.context.active_object
        o.parent = parent
        if i % len(adders) == 2:
            bev = o.modifiers.new("Bevel", "BEVEL")
            bev.width, bev.segments = 0.12, 6
        bpy.ops.object.shade_smooth()
        o.data.materials.append(material(kinds[i % len(kinds)], job["color"] if i % 2 == 0 else job["accent"], f"M{i}"))
        a = i / 7 * math.tau
        r = 2.2 + rnd.random() * 1.4
        for f, ph in ((1, 0.0), (T, 1.0)):
            aa = a + ph * 0.9
            o.location = (math.cos(aa) * r, math.sin(aa) * r * 0.6 + 1.0, 0.3 + rnd.random() * 1.8 if f == 1 else o.location.z)
            o.rotation_euler = (ph * rnd.uniform(1, 3), ph * rnd.uniform(1, 3), ph * rnd.uniform(0.5, 2))
            o.keyframe_insert("location", frame=f)
            o.keyframe_insert("rotation_euler", frame=f)
    return parent


def ease_keys(obj, path, frames_values, interp="BEZIER"):
    for f, v in frames_values:
        setattr(obj, path, v)
        obj.keyframe_insert(path, frame=int(f))
    ad = obj.animation_data
    if ad and ad.action:
        try:
            curves = ad.action.fcurves
        except AttributeError:  # Blender 5 layered actions
            curves = [fc for layer in ad.action.layers for st in layer.strips for cb in st.channelbags for fc in cb.fcurves]
        for fc in curves:
            for kp in fc.keyframe_points:
                kp.interpolation = interp
                if interp == "BEZIER":
                    kp.easing = "AUTO"


def animate_subject(job, ob):
    fps = float(job["fps"])
    T = bpy.context.scene.frame_end
    anim = job.get("animation") or {"title": "rise", "logo": "spin-in", "turntable": "turntable", "abstract": "float"}[job["preset"]]
    f_in = 1 + int(fps * min(1.6, float(job["duration"]) * 0.35))
    if anim == "rise":
        base = ob.location.copy()
        ease_keys(ob, "location", [(1, (base.x, base.y, base.z - 1.6)), (f_in, tuple(base))])
        r0 = ob.rotation_euler.copy()
        ease_keys(ob, "rotation_euler", [(1, (r0.x - 0.9, r0.y, r0.z)), (f_in, tuple(r0))])
    elif anim == "spin-in":
        r0 = ob.rotation_euler.copy()
        ease_keys(ob, "rotation_euler", [(1, (r0.x, r0.y, r0.z - math.pi * 1.5)), (f_in, tuple(r0))])
        ease_keys(ob, "scale", [(1, tuple(v * 0.4 for v in ob.scale)), (f_in, tuple(ob.scale))])
    elif anim == "flip":
        r0 = ob.rotation_euler.copy()
        ease_keys(ob, "rotation_euler", [(1, (r0.x - math.pi, r0.y, r0.z)), (f_in, tuple(r0))])
    elif anim == "turntable":
        r0 = ob.rotation_euler.copy()
        ease_keys(ob, "rotation_euler", [(1, tuple(r0)), (T + 1, (r0.x, r0.y, r0.z + math.tau))], interp="LINEAR")
    # float / abstract: shapes animate themselves


def camera(job, subject):
    cam_d = bpy.data.cameras.new("Camera")
    cam_d.lens = 50
    cam = bpy.data.objects.new("Camera", cam_d)
    bpy.context.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    pivot = bpy.data.objects.new("CameraPivot", None)
    bpy.context.collection.objects.link(pivot)
    cam.parent = pivot
    target = bpy.data.objects.new("Focus", None)
    bpy.context.collection.objects.link(target)
    target.location = (0, 0, {"turntable": 1.3, "abstract": 1.0}.get(job["preset"], 1.0))
    tr = cam.constraints.new("TRACK_TO")
    tr.target = target
    tr.track_axis, tr.up_axis = "TRACK_NEGATIVE_Z", "UP_Y"
    dist = 9.5 if job["preset"] != "abstract" else 11
    T = bpy.context.scene.frame_end
    mode = job["camera"]
    if mode == "push-in":
        ease_keys(cam, "location", [(1, (0, -dist * 1.2, 2.2)), (T, (0, -dist * 0.95, 1.8))])
    elif mode == "dolly":
        ease_keys(cam, "location", [(1, (-2.5, -dist, 2.0)), (T, (2.5, -dist, 2.0))])
    elif mode == "orbit":
        cam.location = (0, -dist, 2.2)
        pivot.location = (0, 0, 0)
        ease_keys(pivot, "rotation_euler", [(1, (0, 0, math.radians(-25))), (T, (0, 0, math.radians(25)))])
    else:
        cam.location = (0, -dist, 2.0)
    if job.get("dof"):
        cam_d.dof.use_dof = True
        cam_d.dof.focus_object = target
        cam_d.dof.aperture_fstop = float(job.get("fstop", 2.8))
    return cam


def main(argv):
    job = parse_args(argv)
    log(f"blender {bpy.app.version_string} job {job['preset']}")
    reset()
    setup_render(job)
    world(job)
    studio_lights(job)
    p = job["preset"]
    if p in ("title", "logo"):
        ob = import_svg_extruded(job)
        ob.location.z = 1.0
        backdrop(job, z=-0.3)
    elif p == "turntable":
        ob = import_glb(job)
        backdrop(job, z=0.0)
    elif p == "abstract":
        ob = abstract_shapes(job)
        backdrop(job, z=-1.2)
    else:
        raise SystemExit(f"unknown preset {p}")
    animate_subject(job, ob)
    camera(job, ob)
    if job.get("blend_path"):
        os.makedirs(os.path.dirname(job["blend_path"]), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=job["blend_path"])
        log("saved " + job["blend_path"])
    if job.get("render", True):
        bpy.ops.render.render(animation=True)
        log("rendered frames " + job["frames_dir"])


if __name__ == "__main__" and bpy is not None:
    try:
        main(sys.argv)
    except SystemExit:
        raise
    except Exception as e:  # make the failure readable for the caller
        import traceback
        traceback.print_exc()
        log("ERROR " + str(e))
        sys.exit(2)
