import bpy, math, os, json, tempfile, threading, queue, subprocess
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ExportHelper

from .typo import (STYLES, parse_srt, parse_time_str,
                   make_material, build_keyframes,
                   make_simple_mat, make_bg_plane, build_plane_keyframes)

# Module-level state for the active extraction so the cancel operator can reach it
_active_extraction = None


# ── extraction subprocess state ───────────────────────────────────────────────

class _ExtractionState:
    def __init__(self):
        self.process    = None
        self._log_queue = queue.Queue()
        self._thread    = None

    def start(self, cmd):
        self.process = subprocess.Popen(
            cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL
        )
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self):
        for raw in self.process.stderr:
            self._log_queue.put(raw.decode('utf-8', errors='replace').rstrip())
        self.process.stderr.close()

    def drain(self):
        lines = []
        try:
            while True:
                lines.append(self._log_queue.get_nowait())
        except queue.Empty:
            pass
        return lines

    def poll(self):
        return self.process is not None and self.process.poll() is not None

    def kill(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()


# ── audio extraction operator ─────────────────────────────────────────────────

class LVB_OT_extract_audio(Operator):
    bl_idname  = "lvb.extract_audio"
    bl_label   = "Extract & Transcribe"
    bl_description = "Separate vocals and transcribe to word-level timestamps (runs in background)"

    _state      = None
    _timer      = None
    _json_path  = None
    _out_dir    = None
    _audio_stem = None

    def modal(self, context, event):
        global _active_extraction
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        p = context.scene.lvb

        for line in self._state.drain():
            if '[MODEL]'      in line: p.extract_stage = "Loading models..."
            elif '[SEPARATE]' in line: p.extract_stage = "Separating vocals..."
            elif '[TRANSCRIBE]' in line: p.extract_stage = "Transcribing..."
            elif '[ALIGN]'    in line: p.extract_stage = "Aligning timestamps..."
            elif '[DONE]'     in line: p.extract_stage = "Finishing..."
            elif '[ERROR]'    in line: p.extract_stage = line

        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

        if not self._state.poll():
            return {'PASS_THROUGH'}

        # Process finished
        context.window_manager.event_timer_remove(self._timer)
        self._timer = None
        p.is_extracting = False
        _active_extraction = None

        rc = self._state.process.returncode
        if rc != 0:
            p.extract_stage = f"Failed (exit code {rc})"
            self.report({'ERROR'}, f"Pipeline failed with exit code {rc}")
            self._cleanup()
            return {'CANCELLED'}

        return self._load_words(context)

    def invoke(self, context, event):
        global _active_extraction
        p = context.scene.lvb

        if not p.audio_path:
            self.report({'ERROR'}, "No audio file selected.")
            return {'CANCELLED'}

        audio_path = bpy.path.abspath(p.audio_path)
        if not os.path.isfile(audio_path):
            self.report({'ERROR'}, f"File not found: {audio_path}")
            return {'CANCELLED'}

        prefs      = context.preferences.addons[__package__].preferences
        python_bin = prefs.venv_python
        if not os.path.isfile(python_bin):
            self.report({'ERROR'}, f"Python binary not found: {python_bin}\n"
                                   "Check addon preferences.")
            return {'CANCELLED'}

        script_path = os.path.join(os.path.dirname(__file__), "ml_pipeline.py")

        tmp_fd, json_path = tempfile.mkstemp(suffix='.json', prefix='lvb_')
        os.close(tmp_fd)
        self._json_path = json_path

        # Output folder named after audio, placed at Blender project root
        # (falls back to alongside the audio file if the project isn't saved yet)
        audio_stem  = os.path.splitext(os.path.basename(audio_path))[0]
        blend_dir   = os.path.dirname(bpy.data.filepath)
        base_dir    = blend_dir if blend_dir else os.path.dirname(audio_path)
        out_dir     = os.path.join(base_dir, audio_stem)
        os.makedirs(out_dir, exist_ok=True)
        vocals_out       = os.path.join(out_dir, "vocals.wav")
        self._out_dir    = out_dir
        self._audio_stem = audio_stem

        cmd = [python_bin, script_path, audio_path,
               "--output", json_path,
               "--vocals-out", vocals_out]

        self._state = _ExtractionState()
        self._state.start(cmd)
        _active_extraction = self._state

        p.is_extracting = True
        p.extract_stage = "Starting..."

        self._timer = context.window_manager.event_timer_add(0.5, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        global _active_extraction
        if self._state:
            self._state.kill()
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
        context.scene.lvb.is_extracting = False
        context.scene.lvb.extract_stage = "Cancelled"
        _active_extraction = None

    def _cleanup(self):
        if self._json_path and os.path.exists(self._json_path):
            try:
                os.unlink(self._json_path)
            except OSError:
                pass

    @staticmethod
    def _fmt_srt(t):
        ms = int((t - int(t)) * 1000)
        s  = int(t) % 60
        m  = (int(t) // 60) % 60
        h  = int(t) // 3600
        return f"{h:02}:{m:02}:{s:02},{ms:03}"

    def _write_srt(self, words):
        srt_path = os.path.join(self._out_dir, self._audio_stem + ".srt")
        with open(srt_path, 'w', encoding='utf-8') as f:
            for i, w in enumerate(words, 1):
                f.write(f"{i}\n")
                f.write(f"{self._fmt_srt(w['start'])} --> {self._fmt_srt(w['end'])}\n")
                f.write(f"{w['word']}\n\n")
        return srt_path

    def _load_words(self, context):
        p = context.scene.lvb
        try:
            with open(self._json_path) as f:
                words = json.load(f)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to read output JSON: {e}")
            self._cleanup()
            return {'CANCELLED'}
        finally:
            self._cleanup()

        # Write word-level SRT and point the SRT importer at it
        try:
            srt_path  = self._write_srt(words)
            p.srt_path = srt_path
        except Exception as e:
            self.report({'WARNING'}, f"Could not write SRT: {e}")

        p.lines.clear()
        mode = p.segment_mode
        n    = p.group_size if mode == 'GROUP' else 1

        chunks = [words[i:i + n] for i in range(0, len(words), n)]
        for chunk in chunks:
            t_start = chunk[0]['start']
            t_end   = chunk[-1]['end']
            mn      = int(t_start) // 60
            emn     = int(t_end)   // 60
            item    = p.lines.add()
            item.time_str = f"{mn}:{t_start - mn * 60:05.2f}"
            item.end_str  = f"{emn}:{t_end - emn * 60:05.2f}"
            item.style    = 'DEFAULT'
            item.text     = ' '.join(w['word'] for w in chunk)

        p.line_index    = 0
        p.extract_stage = f"Done — {len(p.lines)} lines loaded"
        self.report({'INFO'}, f"Loaded {len(p.lines)} lines from audio.")

        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

        return {'FINISHED'}


class LVB_OT_cancel_extract(Operator):
    bl_idname    = "lvb.cancel_extract"
    bl_label     = "Cancel Extraction"
    bl_description = "Stop the running pipeline"

    def execute(self, context):
        global _active_extraction
        if _active_extraction:
            _active_extraction.kill()
            _active_extraction = None
        context.scene.lvb.is_extracting = False
        context.scene.lvb.extract_stage = "Cancelled"
        return {'FINISHED'}


# ── animation / lyric operators (ported from typo-mv) ────────────────────────

class LVB_OT_generate(Operator):
    bl_idname   = "lvb.generate"
    bl_label    = "Generate Animation"
    bl_description = "Create animated text objects from the lyric list"
    bl_options  = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        p     = scene.lvb
        fps   = scene.render.fps

        if not p.lines:
            self.report({'ERROR'}, "No lyric lines.")
            return {'CANCELLED'}

        entries = []
        for i, line in enumerate(p.lines):
            if not line.text.strip():
                continue
            frame     = parse_time_str(line.time_str, i, fps, p.auto_spacing)
            end_frame = parse_time_str(line.end_str, i, fps, p.auto_spacing) if line.end_str.strip() else None
            style     = line.style if line.style != 'DEFAULT' else None
            entries.append((frame, end_frame, line.text.strip(), style))

        if not entries:
            self.report({'ERROR'}, "No valid lines to generate.")
            return {'CANCELLED'}

        font = None
        path = bpy.path.abspath(p.font_path)
        if p.font_path and os.path.isfile(path):
            font = bpy.data.fonts.load(path)

        world = scene.world or bpy.data.worlds.new("World")
        scene.world = world
        world.use_nodes = True
        bg = world.node_tree.nodes.get('Background')
        if bg is None:
            bg  = world.node_tree.nodes.new('ShaderNodeBackground')
            out = world.node_tree.nodes.get('World Output') or \
                  world.node_tree.nodes.new('ShaderNodeOutputWorld')
            world.node_tree.links.new(bg.outputs[0], out.inputs['Surface'])
        bg.inputs['Color'].default_value    = (*p.bg_color, 1.0)
        bg.inputs['Strength'].default_value = 1.0

        for o in list(bpy.data.objects):
            if (o.name.startswith("LVB_line_") or
                    o.name.startswith("LVB_fill_") or
                    o.name.startswith("LVB_outline_")):
                bpy.data.objects.remove(o, do_unlink=True)
        for m in list(bpy.data.materials):
            if (m.name.startswith("LVB_line_") or
                    m.name.startswith("LVB_fill_") or
                    m.name.startswith("LVB_outline_")):
                bpy.data.materials.remove(m)

        tf  = p.transition_frames
        hf  = p.hold_frames
        last_start, last_end = entries[-1][0], entries[-1][1]
        scene.frame_end = (last_end if last_end is not None else last_start + hf + tf * 2) + 30
        col = context.collection or scene.collection

        for i, (start_frame, end_frame, body, line_style) in enumerate(entries):
            name          = f"LVB_line_{i:03d}"
            curve         = bpy.data.curves.new(name, type='FONT')
            curve.body    = body
            curve.align_x = p.align
            curve.align_y = 'CENTER'
            curve.size    = p.font_size
            curve.extrude = 0.0
            if font:
                curve.font = font

            obj                = bpy.data.objects.new(name, curve)
            obj.location       = (0.0, 0.0, 0.0)
            obj.rotation_euler = (math.pi / 2, 0.0, 0.0)
            col.objects.link(obj)

            mat = make_material(name, p.text_color, p.emission_strength)
            curve.materials.append(mat)

            t_in   = start_frame
            t_hold = t_in + tf
            if end_frame is not None:
                t_end = end_frame
                if i + 1 < len(entries):
                    t_end = min(t_end, entries[i + 1][0])
                t_out = t_end - tf
                if t_out < t_hold:
                    mid    = (t_in + t_end) // 2
                    t_hold = mid
                    t_out  = mid
            else:
                if i + 1 < len(entries):
                    next_start = entries[i + 1][0]
                    t_out = max(t_hold, min(t_hold + hf, next_start - tf))
                else:
                    t_out = t_hold + hf
                t_end = t_out + tf
            build_keyframes(obj, mat, line_style or p.style, t_in, t_hold, t_out, t_end)

            if p.box_enabled:
                context.view_layer.update()
                depsgraph = context.evaluated_depsgraph_get()
                eval_obj  = obj.evaluated_get(depsgraph)
                tw = eval_obj.dimensions.x
                th = eval_obj.dimensions.z
                padx, pady = p.box_padding_x, p.box_padding_y
                fw, fh = tw + padx * 2, th + pady * 2
                bw     = p.box_border_width if p.box_border_enabled else 0.0
                ow, oh = fw + bw * 2, fh + bw * 2

                fill_name = f"LVB_fill_{i:03d}"
                fill_mesh = make_bg_plane(fill_name, fw, fh)
                fill_mat  = make_simple_mat(fill_name, tuple(p.box_fill_color), 1.0)
                fill_obj  = bpy.data.objects.new(fill_name, fill_mesh)
                fill_obj.location = (0.0, 0.0, -0.01)
                fill_mesh.materials.append(fill_mat)
                col.objects.link(fill_obj)
                fill_obj.parent = obj
                build_plane_keyframes(fill_obj, fill_mat, t_in, t_hold, t_out, t_end,
                                      max_alpha=p.box_opacity)

                if p.box_border_enabled:
                    out_name = f"LVB_outline_{i:03d}"
                    out_mesh = make_bg_plane(out_name, ow, oh)
                    out_mat  = make_simple_mat(out_name, tuple(p.box_border_color), 1.0)
                    out_obj  = bpy.data.objects.new(out_name, out_mesh)
                    out_obj.location = (0.0, 0.0, -0.02)
                    out_mesh.materials.append(out_mat)
                    col.objects.link(out_obj)
                    out_obj.parent = obj
                    build_plane_keyframes(out_obj, out_mat, t_in, t_hold, t_out, t_end,
                                         max_alpha=p.box_border_opacity)

        scene.frame_set(entries[0][0])
        self.report({'INFO'}, f"Generated {len(entries)} lines.")
        return {'FINISHED'}


class LVB_OT_load_srt(Operator):
    bl_idname   = "lvb.load_srt"
    bl_label    = "Load SRT"
    bl_description = "Parse the SRT file and populate the lyric list"
    bl_options  = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p    = context.scene.lvb
        path = bpy.path.abspath(p.srt_path)
        if not p.srt_path or not os.path.isfile(path):
            self.report({'ERROR'}, "SRT file not found.")
            return {'CANCELLED'}
        entries = parse_srt(path)
        if not entries:
            self.report({'ERROR'}, "No subtitles found in SRT.")
            return {'CANCELLED'}
        p.lines.clear()
        for t_start, t_end, text in entries:
            mn            = int(t_start) // 60
            emn           = int(t_end)   // 60
            item          = p.lines.add()
            item.time_str = f"{mn}:{t_start - mn * 60:05.2f}"
            item.end_str  = f"{emn}:{t_end - emn * 60:05.2f}"
            item.style    = 'DEFAULT'
            item.text     = text
        p.line_index = 0
        self.report({'INFO'}, f"Loaded {len(entries)} lines.")
        return {'FINISHED'}


class LVB_OT_lines_clear(Operator):
    bl_idname   = "lvb.lines_clear"
    bl_label    = "Clear Lyrics"
    bl_description = "Remove all lines from the lyric list"
    bl_options  = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = context.scene.lvb
        p.lines.clear()
        p.line_index = 0
        return {'FINISHED'}


class LVB_OT_line_add(Operator):
    bl_idname  = "lvb.line_add"
    bl_label   = "Add Line"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p        = context.scene.lvb
        item     = p.lines.add()
        item.text  = "New lyric line"
        item.style = 'DEFAULT'
        p.line_index = len(p.lines) - 1
        return {'FINISHED'}


class LVB_OT_line_remove(Operator):
    bl_idname  = "lvb.line_remove"
    bl_label   = "Remove Line"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = context.scene.lvb
        if not p.lines:
            return {'CANCELLED'}
        p.lines.remove(p.line_index)
        p.line_index = max(0, min(p.line_index, len(p.lines) - 1))
        return {'FINISHED'}


class LVB_OT_line_move(Operator):
    bl_idname  = "lvb.line_move"
    bl_label   = "Move Line"
    bl_options = {'REGISTER', 'UNDO'}
    direction: EnumProperty(items=[('UP', 'Up', ''), ('DOWN', 'Down', '')])

    def execute(self, context):
        p   = context.scene.lvb
        idx = p.line_index
        if self.direction == 'UP' and idx > 0:
            p.lines.move(idx, idx - 1); p.line_index -= 1
        elif self.direction == 'DOWN' and idx < len(p.lines) - 1:
            p.lines.move(idx, idx + 1); p.line_index += 1
        return {'FINISHED'}


class LVB_OT_set_line_style(Operator):
    bl_idname   = "lvb.set_line_style"
    bl_label    = "Insert Style"
    bl_description = "Apply this style to the currently selected lyric line"
    style: StringProperty()

    def execute(self, context):
        p = context.scene.lvb
        targets = [i for i, line in enumerate(p.lines) if line.selected]
        if not targets:
            if not (0 <= p.line_index < len(p.lines)):
                self.report({'WARNING'}, "No line selected.")
                return {'CANCELLED'}
            targets = [p.line_index]
        for i in targets:
            p.lines[i].style = self.style
        return {'FINISHED'}


class LVB_OT_toggle_preview(Operator):
    bl_idname   = "lvb.toggle_preview"
    bl_label    = "Toggle Preview"
    bl_description = "Preview this style — click again to dismiss"
    bl_options  = {'REGISTER', 'UNDO'}
    style: StringProperty()

    def _clear(self):
        for o in list(bpy.data.objects):
            if o.name == "LVB_Preview":
                bpy.data.objects.remove(o, do_unlink=True)
        for m in list(bpy.data.materials):
            if m.name == "LVB_Preview":
                bpy.data.materials.remove(m)

    def execute(self, context):
        scene = context.scene
        p     = scene.lvb
        self._clear()
        if p.preview_style == self.style:
            p.preview_style = ''
            return {'FINISHED'}
        p.preview_style = self.style

        curve         = bpy.data.curves.new("LVB_Preview", type='FONT')
        curve.body    = self.style
        curve.align_x = 'CENTER'
        curve.align_y = 'CENTER'
        curve.size    = p.font_size
        if p.font_path:
            path = bpy.path.abspath(p.font_path)
            if os.path.isfile(path):
                curve.font = bpy.data.fonts.load(path)

        obj                = bpy.data.objects.new("LVB_Preview", curve)
        obj.location       = (0.0, 0.0, 0.0)
        obj.rotation_euler = (math.pi / 2, 0.0, 0.0)
        scene.collection.objects.link(obj)

        mat = make_material("LVB_Preview", p.text_color, p.emission_strength)
        curve.materials.append(mat)

        t_in, tf, hf = 1, 10, 40
        build_keyframes(obj, mat, self.style,
                        t_in, t_in + tf, t_in + tf + hf, t_in + tf + hf + tf)

        scene.frame_start = 1
        scene.frame_end   = t_in + tf * 2 + hf + 10
        scene.frame_set(1)
        self.report({'INFO'}, "Press Space to play. Click again to dismiss.")
        return {'FINISHED'}


class LVB_OT_sync_appearance(Operator):
    bl_idname   = "lvb.sync_appearance"
    bl_label    = "Sync to All Lines"
    bl_description = "Push current font, size, color, and emission to all generated objects"
    bl_options  = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        p     = scene.lvb
        font  = None
        if p.font_path:
            path = bpy.path.abspath(p.font_path)
            if os.path.isfile(path):
                font = bpy.data.fonts.load(path)

        world = scene.world
        if world and world.use_nodes:
            bg = world.node_tree.nodes.get('Background')
            if bg:
                bg.inputs['Color'].default_value    = (*p.bg_color, 1.0)
                bg.inputs['Strength'].default_value = 1.0

        count = 0
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
            count += 1

        self.report({'INFO'}, f"Synced {count} objects.")
        return {'FINISHED'}


class LVB_OT_setup_render(Operator):
    bl_idname   = "lvb.setup_render"
    bl_label    = "Setup TikTok Render (1080×1920)"
    bl_description = "1080×1920 @ 30fps, orthographic camera"
    bl_options  = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        scene.render.resolution_x     = 1080
        scene.render.resolution_y     = 1920
        scene.render.fps              = 30
        scene.render.film_transparent = False

        for name in ("Cube", "Light", "Camera"):
            obj = bpy.data.objects.get(name)
            if obj:
                bpy.data.objects.remove(obj, do_unlink=True)

        for o in list(bpy.data.objects):
            if o.name == "LVB_Camera":
                bpy.data.objects.remove(o, do_unlink=True)
        for c in list(bpy.data.cameras):
            if c.name == "LVB_Camera":
                bpy.data.cameras.remove(c)

        cam             = bpy.data.cameras.new("LVB_Camera")
        cam.type        = 'ORTHO'
        cam.ortho_scale = 10.0
        cam_obj         = bpy.data.objects.new("LVB_Camera", cam)
        cam_obj.location       = (0.0, -12.0, 0.0)
        cam_obj.rotation_euler = (math.pi / 2, 0.0, 0.0)
        scene.collection.objects.link(cam_obj)
        scene.camera = cam_obj
        self.report({'INFO'}, "1080×1920 @ 30fps, ortho camera created.")
        return {'FINISHED'}


class LVB_OT_clear(Operator):
    bl_idname   = "lvb.clear"
    bl_label    = "Clear All"
    bl_description = "Remove all generated LVB objects from the scene"
    bl_options  = {'REGISTER', 'UNDO'}

    def execute(self, context):
        n = 0
        for o in list(bpy.data.objects):
            if (o.name.startswith("LVB_line_") or
                    o.name.startswith("LVB_fill_") or
                    o.name.startswith("LVB_outline_") or
                    o.name == "LVB_Preview"):
                bpy.data.objects.remove(o, do_unlink=True); n += 1
        for m in list(bpy.data.materials):
            if (m.name.startswith("LVB_line_") or
                    m.name.startswith("LVB_fill_") or
                    m.name.startswith("LVB_outline_") or
                    m.name == "LVB_Preview"):
                bpy.data.materials.remove(m)
        context.scene.lvb.preview_style = ''
        self.report({'INFO'}, f"Removed {n} objects.")
        return {'FINISHED'}


class LVB_OT_save_srt(Operator, ExportHelper):
    bl_idname    = "lvb.save_srt"
    bl_label     = "Save to SRT"
    bl_description = "Export the current lyric list as an SRT subtitle file"
    bl_options   = {'REGISTER'}
    filename_ext = ".srt"
    filter_glob: StringProperty(default="*.srt", options={'HIDDEN'})

    @staticmethod
    def _to_ts(t):
        ms = int((t % 1) * 1000)
        s  = int(t) % 60
        m  = (int(t) // 60) % 60
        h  = int(t) // 3600
        return f"{h:02}:{m:02}:{s:02},{ms:03}"

    def execute(self, context):
        p   = context.scene.lvb
        fps = context.scene.render.fps
        if not p.lines:
            self.report({'ERROR'}, "No lyric lines to export.")
            return {'CANCELLED'}

        blocks = []
        idx = 1
        for i, line in enumerate(p.lines):
            if not line.text.strip():
                continue
            t_start = parse_time_str(line.time_str, i, fps, p.auto_spacing) / fps
            if line.end_str.strip():
                t_end = parse_time_str(line.end_str, i, fps, p.auto_spacing) / fps
            else:
                t_end = t_start + p.hold_frames / fps
            blocks.append(
                f"{idx}\n{self._to_ts(t_start)} --> {self._to_ts(t_end)}\n{line.text.strip()}"
            )
            idx += 1

        path = self.filepath
        if not path.lower().endswith('.srt'):
            path += '.srt'
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n\n'.join(blocks) + '\n')

        self.report({'INFO'}, f"Saved {len(blocks)} lines to {os.path.basename(path)}")
        return {'FINISHED'}


# ── registration ──────────────────────────────────────────────────────────────

classes = [
    LVB_OT_extract_audio,
    LVB_OT_cancel_extract,
    LVB_OT_generate,
    LVB_OT_load_srt,
    LVB_OT_lines_clear,
    LVB_OT_line_add,
    LVB_OT_line_remove,
    LVB_OT_line_move,
    LVB_OT_set_line_style,
    LVB_OT_toggle_preview,
    LVB_OT_sync_appearance,
    LVB_OT_setup_render,
    LVB_OT_clear,
    LVB_OT_save_srt,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
