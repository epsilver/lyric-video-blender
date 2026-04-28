"""
Animation helpers, property groups, and UIList.
"""
import bpy, math, os, re
from bpy.props import (StringProperty, FloatProperty, EnumProperty,
                       IntProperty, FloatVectorProperty, CollectionProperty,
                       BoolProperty)
from bpy.types import PropertyGroup, UIList


# ── styles ────────────────────────────────────────────────────────────────────

STYLES = [
    ('FADE',        'Fade',        'Fade in and out with no motion'),
    ('SLIDE_UP',    'Slide Up',    'Slides in from below, exits upward'),
    ('SLIDE_DOWN',  'Slide Down',  'Slides in from above, exits downward'),
    ('SLIDE_LEFT',  'Slide Left',  'Slides in from left, exits right'),
    ('SLIDE_RIGHT', 'Slide Right', 'Slides in from right, exits left'),
    ('SCALE',       'Scale',       'Scales up from tiny, shrinks out'),
    ('ZOOM_OUT',    'Zoom Out',    'Starts huge and shrinks to normal size'),
    ('BOUNCE',      'Bounce',      'Drops from above with a small bounce'),
    ('POP',         'Pop',         'Scales up with an overshoot then settles'),
    ('SPIN',        'Spin',        'Full 360 degree Z rotation on entry and exit'),
    ('FLIP',        'Flip',        'Card-flip on the Y axis'),
    ('SWING',       'Swing',       'Pendulum swing into place on Z axis'),
    ('TILT',        'Tilt',        'Tilts in from an angle, straightens out'),
    ('RISE',        'Rise',        'Fades in and slowly floats upward'),
    ('DRIFT',       'Drift',       'Fades in and lazily drifts right'),
    ('SHAKE',       'Shake',       'Rapid horizontal jitter on entry'),
    ('TYPEWRITER',  'Typewriter',  'Characters reveal left to right, fades out'),
    ('WIPE',        'Wipe',        'Curtain wipes in from left, wipes back out'),
    ('GLITCH',      'Glitch',      'Random position jitter burst then snaps to rest'),
    ('CORRUPT',     'Corrupt',     'Scale and rotation spasms then snaps to rest'),
    ('FLICKER',     'Flicker',     'Rapid visibility strobe on entry'),
    ('STATIC',      'Static',      'Alpha strobes erratically before settling'),
]

STYLE_ITEMS_WITH_DEFAULT = [('DEFAULT', '— (use default)', 'Use the panel default style')] + \
                           [(s[0], s[1], s[2]) for s in STYLES]

_CUSTOM_ALPHA = {'TYPEWRITER', 'WIPE', 'FLICKER', 'STATIC'}


# ── parsing ───────────────────────────────────────────────────────────────────

def parse_srt(filepath):
    with open(filepath, 'r', encoding='utf-8-sig', errors='replace') as f:
        content = f.read()
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    entries = []
    for block in re.split(r'\n{2,}', content.strip()):
        lines = block.strip().splitlines()
        ts = None; text_start = 0
        for idx, line in enumerate(lines):
            m = re.match(
                r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})', line)
            if m:
                ts = m; text_start = idx + 1; break
        if ts is None:
            continue
        h,  mn,  s,  ms  = int(ts.group(1)), int(ts.group(2)), int(ts.group(3)), int(ts.group(4))
        eh, emn, es, ems = int(ts.group(5)), int(ts.group(6)), int(ts.group(7)), int(ts.group(8))
        t_start = h  * 3600 + mn  * 60 + s  + ms  / 1000.0
        t_end   = eh * 3600 + emn * 60 + es + ems / 1000.0
        raw = ' '.join(lines[text_start:])
        raw = re.sub(r'\{[^}]*\}', '', raw)
        raw = re.sub(r'<[^>]+>', '', raw)
        text = raw.strip()
        if text:
            entries.append((t_start, t_end, text))
    return entries


def parse_time_str(time_str, index, fps, spacing):
    s = time_str.strip()
    if not s:
        return round(index * spacing * fps)
    m = re.match(r'^(\d+):(\d{2}(?:\.\d+)?)$', s)
    if m:
        return round((int(m.group(1)) * 60 + float(m.group(2))) * fps)
    try:
        return round(float(s) * fps)
    except ValueError:
        return round(index * spacing * fps)


def compute_line_timing(p, scene, target_idx):
    """Return (t_in, t_hold, t_out, t_end) for the line at target_idx, or None."""
    fps = scene.render.fps
    tf  = p.transition_frames
    hf  = p.hold_frames

    frames     = []
    end_frames = []
    orig       = []
    for i, line in enumerate(p.lines):
        if line.text.strip():
            frames.append(parse_time_str(line.time_str, i, fps, p.auto_spacing))
            ef = parse_time_str(line.end_str, i, fps, p.auto_spacing) if line.end_str.strip() else None
            end_frames.append(ef)
            orig.append(i)

    if target_idx not in orig:
        return None

    pos       = orig.index(target_idx)
    t_in      = frames[pos]
    t_hold    = t_in + tf
    end_frame = end_frames[pos]

    if end_frame is not None:
        t_end = end_frame
        if pos + 1 < len(frames):
            t_end = min(t_end, frames[pos + 1])
        t_out = t_end - tf
        if t_out < t_hold:
            mid    = (t_in + t_end) // 2
            t_hold = mid
            t_out  = mid
    else:
        if pos + 1 < len(frames):
            t_out = max(t_hold, min(t_hold + hf, frames[pos + 1] - tf))
        else:
            t_out = t_hold + hf
        t_end = t_out + tf

    return t_in, t_hold, t_out, t_end


# ── text material ─────────────────────────────────────────────────────────────

def make_material(name, rgb, emission_strength=2.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    if hasattr(mat, 'blend_method'):  mat.blend_method  = 'BLEND'
    if hasattr(mat, 'shadow_method'): mat.shadow_method = 'NONE'
    nt = mat.node_tree
    nt.nodes.clear()

    alpha_val = nt.nodes.new('ShaderNodeValue'); alpha_val.name = "Alpha"
    alpha_val.outputs[0].default_value = 1.0

    threshold = nt.nodes.new('ShaderNodeValue'); threshold.name = "Threshold"
    threshold.outputs[0].default_value = 100.0

    geo = nt.nodes.new('ShaderNodeNewGeometry')
    sep = nt.nodes.new('ShaderNodeSeparateXYZ')
    nt.links.new(geo.outputs['Position'], sep.inputs[0])

    sub = nt.nodes.new('ShaderNodeMath'); sub.operation = 'SUBTRACT'
    nt.links.new(threshold.outputs[0], sub.inputs[0])
    nt.links.new(sep.outputs['X'],     sub.inputs[1])

    step = nt.nodes.new('ShaderNodeMath'); step.operation = 'GREATER_THAN'
    step.inputs[1].default_value = 0.0
    nt.links.new(sub.outputs[0], step.inputs[0])

    mult = nt.nodes.new('ShaderNodeMath'); mult.operation = 'MULTIPLY'
    nt.links.new(step.outputs[0],      mult.inputs[0])
    nt.links.new(alpha_val.outputs[0], mult.inputs[1])

    emission = nt.nodes.new('ShaderNodeEmission'); emission.name = "Emission"
    emission.inputs['Color'].default_value    = (*rgb, 1.0)
    emission.inputs['Strength'].default_value = emission_strength

    transparent = nt.nodes.new('ShaderNodeBsdfTransparent')
    mix         = nt.nodes.new('ShaderNodeMixShader')
    output      = nt.nodes.new('ShaderNodeOutputMaterial')

    nt.links.new(mult.outputs[0],        mix.inputs[0])
    nt.links.new(transparent.outputs[0], mix.inputs[1])
    nt.links.new(emission.outputs[0],    mix.inputs[2])
    nt.links.new(mix.outputs[0],         output.inputs['Surface'])
    return mat


# ── box / border material & mesh ──────────────────────────────────────────────

def make_simple_mat(name, rgb, emission_strength=1.0):
    """Flat emission material with an Alpha node for fade animation."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    if hasattr(mat, 'blend_method'):  mat.blend_method  = 'BLEND'
    if hasattr(mat, 'shadow_method'): mat.shadow_method = 'NONE'
    nt = mat.node_tree
    nt.nodes.clear()

    alpha_val = nt.nodes.new('ShaderNodeValue'); alpha_val.name = "Alpha"
    alpha_val.outputs[0].default_value = 1.0

    emission = nt.nodes.new('ShaderNodeEmission'); emission.name = "Emission"
    emission.inputs['Color'].default_value    = (*rgb, 1.0)
    emission.inputs['Strength'].default_value = emission_strength

    transparent = nt.nodes.new('ShaderNodeBsdfTransparent')
    mix         = nt.nodes.new('ShaderNodeMixShader')
    output      = nt.nodes.new('ShaderNodeOutputMaterial')

    nt.links.new(alpha_val.outputs[0],   mix.inputs[0])
    nt.links.new(transparent.outputs[0], mix.inputs[1])
    nt.links.new(emission.outputs[0],    mix.inputs[2])
    nt.links.new(mix.outputs[0],         output.inputs['Surface'])
    return mat


def make_bg_plane(name, w, h):
    """Flat mesh plane sized w × h, lying in the XY plane, with a UV map."""
    mesh = bpy.data.meshes.new(name)
    hw, hh = w / 2, h / 2
    mesh.from_pydata(
        [(-hw, -hh, 0), (hw, -hh, 0), (hw, hh, 0), (-hw, hh, 0)],
        [],
        [(0, 1, 2, 3)],
    )
    mesh.update()
    uv = mesh.uv_layers.new(name="UVMap")
    uv.data[0].uv = (0, 0)
    uv.data[1].uv = (1, 0)
    uv.data[2].uv = (1, 1)
    uv.data[3].uv = (0, 1)
    return mesh


def build_plane_keyframes(obj, mat, t_in, t_hold, t_out, t_end, max_alpha=1.0):
    """Visibility gates + alpha fade for box/border planes."""
    obj.hide_viewport = True;  obj.hide_render = True
    obj.keyframe_insert('hide_viewport', frame=0)
    obj.keyframe_insert('hide_render',   frame=0)
    obj.hide_viewport = False; obj.hide_render = False
    obj.keyframe_insert('hide_viewport', frame=t_in)
    obj.keyframe_insert('hide_render',   frame=t_in)
    obj.hide_viewport = True;  obj.hide_render = True
    obj.keyframe_insert('hide_viewport', frame=t_end + 1)
    obj.keyframe_insert('hide_render',   frame=t_end + 1)
    obj.hide_viewport = False; obj.hide_render = False

    kf_alpha(mat, 0.0,       t_in);  kf_alpha(mat, max_alpha, t_hold)
    kf_alpha(mat, max_alpha, t_out); kf_alpha(mat, 0.0,       t_end)
    smooth_fcurves(obj.animation_data)
    if mat.node_tree.animation_data:
        smooth_fcurves(mat.node_tree.animation_data)


# ── keyframe helpers ──────────────────────────────────────────────────────────

def kf_alpha(mat, alpha, frame):
    node = mat.node_tree.nodes["Alpha"]
    node.outputs[0].default_value = alpha
    node.outputs[0].keyframe_insert("default_value", frame=frame)


def kf_threshold(mat, value, frame):
    node = mat.node_tree.nodes["Threshold"]
    node.outputs[0].default_value = value
    node.outputs[0].keyframe_insert("default_value", frame=frame)


def iter_fcurves(action):
    if hasattr(action, 'layers') and action.layers:
        for layer in action.layers:
            for strip in layer.strips:
                if hasattr(strip, 'channelbags'):
                    for channelbag in strip.channelbags:
                        yield from channelbag.fcurves
    elif hasattr(action, 'fcurves'):
        yield from action.fcurves


def smooth_fcurves(anim_data):
    if anim_data and anim_data.action:
        for fc in iter_fcurves(anim_data.action):
            for kp in fc.keyframe_points:
                kp.interpolation = 'BEZIER'


def set_linear(anim_data, data_path=None):
    if anim_data and anim_data.action:
        for fc in iter_fcurves(anim_data.action):
            if data_path is None or fc.data_path == data_path:
                for kp in fc.keyframe_points:
                    kp.interpolation = 'LINEAR'


# ── text keyframe builder ─────────────────────────────────────────────────────

def build_keyframes(obj, mat, style, t_in, t_hold, t_out, t_end):
    x, y, z = obj.location
    DZ, DX = 1.8, 6.0
    RX     = math.pi / 2
    WIDE   = 12.0

    obj.hide_viewport = True;  obj.hide_render = True
    obj.keyframe_insert('hide_viewport', frame=0)
    obj.keyframe_insert('hide_render',   frame=0)
    obj.hide_viewport = False; obj.hide_render = False
    obj.keyframe_insert('hide_viewport', frame=t_in)
    obj.keyframe_insert('hide_render',   frame=t_in)
    obj.hide_viewport = True;  obj.hide_render = True
    obj.keyframe_insert('hide_viewport', frame=t_end + 1)
    obj.keyframe_insert('hide_render',   frame=t_end + 1)
    obj.hide_viewport = False; obj.hide_render = False

    if style not in _CUSTOM_ALPHA:
        kf_alpha(mat, 0.0, t_in);  kf_alpha(mat, 1.0, t_hold)
        kf_alpha(mat, 1.0, t_out); kf_alpha(mat, 0.0, t_end)

    def loc(px, py, pz, f):
        obj.location = (px, py, pz); obj.keyframe_insert('location', frame=f)
    def scl(s, f):
        obj.scale = (s, s, s); obj.keyframe_insert('scale', frame=f)
    def rot(rx, ry, rz, f):
        obj.rotation_euler = (rx, ry, rz); obj.keyframe_insert('rotation_euler', frame=f)

    if style == 'SLIDE_UP':
        loc(x, y, z - DZ, t_in);  loc(x, y, z, t_hold)
        loc(x, y, z, t_out);      loc(x, y, z + DZ, t_end)
    elif style == 'SLIDE_DOWN':
        loc(x, y, z + DZ, t_in);  loc(x, y, z, t_hold)
        loc(x, y, z, t_out);      loc(x, y, z - DZ, t_end)
    elif style == 'SLIDE_LEFT':
        loc(x - DX, y, z, t_in);  loc(x, y, z, t_hold)
        loc(x, y, z, t_out);      loc(x + DX, y, z, t_end)
    elif style == 'SLIDE_RIGHT':
        loc(x + DX, y, z, t_in);  loc(x, y, z, t_hold)
        loc(x, y, z, t_out);      loc(x - DX, y, z, t_end)
    elif style == 'SCALE':
        scl(0.01, t_in); scl(1.0, t_hold); scl(1.0, t_out); scl(0.01, t_end)
        obj.scale = (1.0, 1.0, 1.0)
    elif style == 'ZOOM_OUT':
        scl(4.0, t_in); scl(1.0, t_hold); scl(1.0, t_out); scl(0.01, t_end)
        obj.scale = (1.0, 1.0, 1.0)
    elif style == 'BOUNCE':
        loc(x, y, z + 2.5,  t_in)
        loc(x, y, z - 0.15, t_hold - 4)
        loc(x, y, z,        t_hold)
        loc(x, y, z,        t_out)
        loc(x, y, z + 2.5,  t_end)
    elif style == 'POP':
        scl(0.01, t_in); scl(1.18, t_hold - 4)
        scl(1.0, t_hold); scl(1.0, t_out); scl(0.01, t_end)
        obj.scale = (1.0, 1.0, 1.0)
    elif style == 'SPIN':
        rot(RX, 0,  math.pi * 2, t_in);  rot(RX, 0, 0, t_hold)
        rot(RX, 0,  0, t_out);           rot(RX, 0, -math.pi * 2, t_end)
        obj.rotation_euler = (RX, 0, 0)
    elif style == 'FLIP':
        rot(RX, -math.pi / 2, 0, t_in); rot(RX, 0, 0, t_hold)
        rot(RX,  0, 0, t_out);          rot(RX, math.pi / 2, 0, t_end)
        obj.rotation_euler = (RX, 0, 0)
    elif style == 'SWING':
        rot(RX, 0,  math.pi / 4,  t_in)
        rot(RX, 0, -math.pi / 12, t_hold - 4)
        rot(RX, 0,  0,            t_hold)
        rot(RX, 0,  0,            t_out)
        rot(RX, 0, -math.pi / 4,  t_end)
        obj.rotation_euler = (RX, 0, 0)
    elif style == 'TILT':
        rot(RX, 0,  math.pi / 6, t_in); rot(RX, 0, 0, t_hold)
        rot(RX, 0,  0, t_out);          rot(RX, 0, -math.pi / 6, t_end)
        obj.rotation_euler = (RX, 0, 0)
    elif style == 'RISE':
        rise = DZ * 0.8
        loc(x, y, z,              t_in);  loc(x, y, z + rise * 0.3, t_hold)
        loc(x, y, z + rise * 0.6, t_out); loc(x, y, z + rise,       t_end)
    elif style == 'DRIFT':
        d = DX * 0.35
        loc(x - d * 0.3, y, z, t_in); loc(x,           y, z, t_hold)
        loc(x + d * 0.5, y, z, t_out); loc(x + d,      y, z, t_end)
    elif style == 'SHAKE':
        dt = max(2, (t_hold - t_in) // 6)
        for i2, offset in enumerate([0.3, -0.3, 0.2, -0.2, 0.1, 0.0]):
            f = t_in + i2 * dt
            if f <= t_hold:
                loc(x + offset, y, z, f)
        loc(x, y, z, t_hold); loc(x, y, z, t_out); loc(x, y, z, t_end)
    elif style == 'TYPEWRITER':
        kf_alpha(mat, 1.0, t_in); kf_alpha(mat, 1.0, t_hold)
        kf_alpha(mat, 1.0, t_out); kf_alpha(mat, 0.0, t_end)
        kf_threshold(mat, -WIDE, t_in)
        kf_threshold(mat, +WIDE, t_hold)
        kf_threshold(mat, +WIDE, t_end)
    elif style == 'WIPE':
        kf_alpha(mat, 1.0, t_in); kf_alpha(mat, 1.0, t_hold)
        kf_alpha(mat, 1.0, t_out); kf_alpha(mat, 1.0, t_end)
        kf_threshold(mat, -WIDE, t_in);  kf_threshold(mat, +WIDE, t_hold)
        kf_threshold(mat, +WIDE, t_out); kf_threshold(mat, -WIDE, t_end)
    elif style == 'GLITCH':
        jitter = [(0.45,0.25),(-0.3,-0.15),(0.55,0.3),(-0.2,0.12),
                  (0.15,-0.2),(-0.4,0.05),(0.2,0.1),(0.0,0.0)]
        dt = max(1, (t_hold - t_in) // len(jitter))
        for i2, (jx, jz) in enumerate(jitter):
            f = t_in + i2 * dt
            if f <= t_hold:
                loc(x + jx, y, z + jz, f)
        loc(x, y, z, t_hold); loc(x, y, z, t_out); loc(x, y, z, t_end)
    elif style == 'CORRUPT':
        spasms = [(0.8,0.4),(-0.3,-0.25),(1.1,0.15),(-0.15,0.35),
                  (0.6,-0.3),(0.1,0.1),(0.0,0.0)]
        dt = max(1, (t_hold - t_in) // len(spasms))
        for i2, (ss, sr) in enumerate(spasms):
            f = t_in + i2 * dt
            if f <= t_hold:
                obj.scale = (0.5+ss, 0.5+ss, 0.5+ss)
                obj.keyframe_insert('scale', frame=f)
                rot(RX, 0, sr, f)
        scl(1.0, t_hold); rot(RX, 0, 0, t_hold)
        scl(1.0, t_out);  scl(0.01, t_end)
        obj.scale = (1.0, 1.0, 1.0); obj.rotation_euler = (RX, 0, 0)
    elif style == 'FLICKER':
        kf_alpha(mat, 1.0, t_in); kf_alpha(mat, 1.0, t_hold)
        kf_alpha(mat, 1.0, t_out); kf_alpha(mat, 0.0, t_end)
        strobe_count = 8
        dt = max(1, (t_hold - t_in) // (strobe_count * 2))
        for i2 in range(strobe_count * 2):
            f = t_in + i2 * dt
            if f < t_hold:
                obj.hide_viewport = (i2 % 2 == 0); obj.hide_render = (i2 % 2 == 0)
                obj.keyframe_insert('hide_viewport', frame=f)
                obj.keyframe_insert('hide_render',   frame=f)
        obj.hide_viewport = False; obj.hide_render = False
        obj.keyframe_insert('hide_viewport', frame=t_hold)
        obj.keyframe_insert('hide_render',   frame=t_hold)
    elif style == 'STATIC':
        stutter = [0.0,0.9,0.1,0.8,0.0,1.0,0.2,0.85,0.05,0.95,1.0]
        dt = max(1, (t_hold - t_in) // len(stutter))
        for i2, a in enumerate(stutter):
            f = t_in + i2 * dt
            if f <= t_hold:
                kf_alpha(mat, a, f)
        kf_alpha(mat, 1.0, t_hold); kf_alpha(mat, 1.0, t_out); kf_alpha(mat, 0.0, t_end)

    smooth_fcurves(obj.animation_data)
    if mat.node_tree.animation_data:
        smooth_fcurves(mat.node_tree.animation_data)

    if style in ('SHAKE', 'GLITCH', 'CORRUPT'):
        set_linear(obj.animation_data, 'location')
    if style == 'CORRUPT':
        set_linear(obj.animation_data, 'scale')
        set_linear(obj.animation_data, 'rotation_euler')
    if style == 'STATIC' and mat.node_tree.animation_data:
        set_linear(mat.node_tree.animation_data)


# ── live-update callbacks ─────────────────────────────────────────────────────

def _sync_appearance_cb(self, context):
    """Fires when any appearance property on LVBProps changes."""
    if not context or not getattr(context, 'scene', None):
        return
    p = self
    font = None
    if p.font_path:
        path = bpy.path.abspath(p.font_path)
        if os.path.isfile(path):
            try:
                font = bpy.data.fonts.load(path)
            except Exception:
                pass

    for obj in bpy.data.objects:
        if not obj.name.startswith("LVB_line_"):
            continue
        curve = obj.data
        if not hasattr(curve, 'body'):
            continue
        if font:        curve.font    = font
        curve.size    = p.font_size
        curve.align_x = p.align
        if curve.materials:
            mat = curve.materials[0]
            if mat and mat.use_nodes:
                em = mat.node_tree.nodes.get("Emission")
                if em:
                    em.inputs['Color'].default_value    = (*p.text_color, 1.0)
                    em.inputs['Strength'].default_value = p.emission_strength

    scene = context.scene
    if scene.world and scene.world.use_nodes:
        bg = scene.world.node_tree.nodes.get('Background')
        if bg:
            bg.inputs['Color'].default_value = (*p.bg_color, 1.0)


def _sync_box_cb(self, context):
    """Fires when any box/border property on LVBProps changes."""
    if not context or not getattr(context, 'scene', None):
        return
    p = self
    for obj in bpy.data.objects:
        if obj.name.startswith("LVB_fill_"):
            obj.hide_viewport = not p.box_enabled
            obj.hide_render   = not p.box_enabled
            if obj.data.materials:
                mat = obj.data.materials[0]
                if mat and mat.use_nodes:
                    em = mat.node_tree.nodes.get("Emission")
                    if em:
                        em.inputs['Color'].default_value = (*p.box_fill_color, 1.0)
        elif obj.name.startswith("LVB_outline_"):
            visible = p.box_enabled and p.box_border_enabled
            obj.hide_viewport = not visible
            obj.hide_render   = not visible
            if obj.data.materials:
                mat = obj.data.materials[0]
                if mat and mat.use_nodes:
                    em = mat.node_tree.nodes.get("Emission")
                    if em:
                        em.inputs['Color'].default_value = (*p.box_border_color, 1.0)


def _update_line_text_cb(self, context):
    """Fires when a lyric line's text is edited — updates the matching 3D object."""
    if not context or not getattr(context, 'scene', None):
        return
    p = context.scene.lvb
    for i, line in enumerate(p.lines):
        if line != self:
            continue
        obj = bpy.data.objects.get(f"LVB_line_{i:03d}")
        if obj and hasattr(obj.data, 'body'):
            obj.data.body = self.text
        break


def _jump_to_line_cb(self, context):
    if not context or not getattr(context, 'scene', None):
        return
    scene = context.scene
    p = self
    if not (0 <= p.line_index < len(p.lines)):
        return
    line = p.lines[p.line_index]
    if not line.time_str.strip():
        return
    frame = parse_time_str(line.time_str, p.line_index, scene.render.fps, p.auto_spacing)
    scene.frame_current = frame


def _update_line_style_cb(self, context):
    """Fires when a lyric line's style changes — rebuilds keyframes for that object only."""
    if not context or not getattr(context, 'scene', None):
        return
    scene = context.scene
    p     = scene.lvb
    for i, line in enumerate(p.lines):
        if line != self:
            continue
        obj = bpy.data.objects.get(f"LVB_line_{i:03d}")
        if not obj or not obj.data.materials:
            break
        mat    = obj.data.materials[0]
        timing = compute_line_timing(p, scene, i)
        if not timing:
            break
        t_in, t_hold, t_out, t_end = timing
        if obj.animation_data:
            obj.animation_data_clear()
        if mat.node_tree.animation_data:
            mat.node_tree.animation_data_clear()
        style = self.style if self.style != 'DEFAULT' else p.style
        build_keyframes(obj, mat, style, t_in, t_hold, t_out, t_end)
        break


# ── property groups ───────────────────────────────────────────────────────────

class LVBLine(PropertyGroup):
    time_str: StringProperty(
        name="Start",
        description="e.g. 0:03 or 3.5 (seconds). Leave blank to auto-space",
        default="",
    )
    end_str:  StringProperty(name="End",   description="End timestamp", default="")
    style:    EnumProperty(name="Style", items=STYLE_ITEMS_WITH_DEFAULT, default='DEFAULT',
                           update=_update_line_style_cb)
    text:     StringProperty(name="Lyric", default="", update=_update_line_text_cb)
    selected: BoolProperty(name="Selected", default=False)


class LVBProps(PropertyGroup):
    # Lyric list
    lines:         CollectionProperty(type=LVBLine)
    line_index:    IntProperty(default=0, update=_jump_to_line_cb)
    preview_style: StringProperty(default="")

    # Appearance (update= triggers live sync to existing objects)
    font_path: StringProperty(name="Font (.ttf)", subtype='FILE_PATH', default="",
                              update=_sync_appearance_cb)
    style:     EnumProperty(name="Default Style", items=STYLES, default='SLIDE_UP')
    text_color: FloatVectorProperty(name="Text Color", subtype='COLOR', size=3,
                    min=0.0, max=1.0, default=(1.0, 1.0, 1.0),
                    update=_sync_appearance_cb)
    bg_color:   FloatVectorProperty(name="BG Color", subtype='COLOR', size=3,
                    min=0.0, max=1.0, default=(0.05, 0.05, 0.05),
                    update=_sync_appearance_cb)
    font_size:  FloatProperty(name="Font Size", default=1.2, min=0.1, max=10.0,
                              update=_sync_appearance_cb)
    align:      EnumProperty(name="Align",
                    items=[('CENTER','Center',''),('LEFT','Left',''),('RIGHT','Right','')],
                    default='CENTER', update=_sync_appearance_cb)
    emission_strength: FloatProperty(name="Emission Strength", default=2.0, min=0.0, max=50.0,
                                     update=_sync_appearance_cb)

    # Timing
    hold_frames:       IntProperty(name="Hold (frames)", default=55, min=1)
    transition_frames: IntProperty(name="Transition (frames)", default=12, min=1)
    auto_spacing:      FloatProperty(name="Auto-spacing (s)", default=3.0, min=0.1)

    # SRT / UI state
    srt_path:           StringProperty(name="SRT File", subtype='FILE_PATH', default="")
    style_browser_open: BoolProperty(name="Style Browser", default=False)

    # Audio extraction
    audio_path:    StringProperty(name="Audio / Video", subtype='FILE_PATH', default="")
    segment_mode:  EnumProperty(
        name="Mode",
        items=[
            ('WORD',  'Word by Word', 'Each word becomes one lyric line'),
            ('GROUP', 'Group',        'N words per lyric line'),
        ],
        default='WORD',
    )
    group_size:    IntProperty(name="Words per Line", default=3, min=1, max=20)
    is_extracting: BoolProperty(default=False)
    extract_stage: StringProperty(default="")

    # Box / border (update= triggers live colour/visibility sync)
    box_enabled: BoolProperty(name="Background Box", default=False,
                              update=_sync_box_cb)
    box_fill_color: FloatVectorProperty(name="Fill Color", subtype='COLOR', size=3,
                        min=0.0, max=1.0, default=(0.0, 0.0, 0.0),
                        update=_sync_box_cb)
    box_opacity: FloatProperty(name="Fill Opacity", default=0.8, min=0.0, max=1.0)
    box_padding_x: FloatProperty(name="Pad X", default=0.15, min=0.0)
    box_padding_y: FloatProperty(name="Pad Y", default=0.08, min=0.0)
    box_border_enabled: BoolProperty(name="Border", default=False,
                                     update=_sync_box_cb)
    box_border_color: FloatVectorProperty(name="Border Color", subtype='COLOR', size=3,
                          min=0.0, max=1.0, default=(1.0, 1.0, 1.0),
                          update=_sync_box_cb)
    box_border_opacity: FloatProperty(name="Border Opacity", default=1.0, min=0.0, max=1.0)
    box_border_width:   FloatProperty(name="Border Width", default=0.04, min=0.0)


class LVB_UL_lines(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.prop(item, "selected", text="")
            start = item.time_str if item.time_str else "auto"
            time_label = f"{start} → {item.end_str}" if item.end_str else start
            split  = row.split(factor=0.38, align=False)
            split.label(text=time_label)
            split2 = split.split(factor=0.30, align=False)
            split2.label(text=item.style if item.style != 'DEFAULT' else "—")
            split2.label(text=item.text)
        elif self.layout_type == 'GRID':
            layout.label(text=item.text)


classes = [LVBLine, LVBProps, LVB_UL_lines]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.lvb = bpy.props.PointerProperty(type=LVBProps)


def unregister():
    del bpy.types.Scene.lvb
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
