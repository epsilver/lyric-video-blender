import bpy
from bpy.types import Panel
from .typo import STYLES


class LVB_PT_main(Panel):
    bl_label      = "Lyric Video Blender"
    bl_idname     = "LVB_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type= 'UI'
    bl_category   = "LyricVideo"
    bl_options    = {'HIDE_HEADER'}

    def draw(self, context):
        layout = self.layout
        p      = context.scene.lvb

        # ── Extract from Audio ──────────────────────────────────────────────
        box = layout.box()
        box.label(text="Extract from Audio:", icon='SOUND')
        box.prop(p, "audio_path", text="")

        row = box.row(align=True)
        row.prop(p, "segment_mode", expand=True)
        if p.segment_mode == 'GROUP':
            box.prop(p, "group_size")

        if p.is_extracting:
            box.label(text=p.extract_stage, icon='TIME')
            box.operator("lvb.cancel_extract", icon='CANCEL')
        else:
            if p.extract_stage:
                box.label(text=p.extract_stage,
                          icon='CHECKMARK' if "Done" in p.extract_stage else 'ERROR')
            box.operator("lvb.extract_audio", icon='PLAY')

        layout.separator()

        # ── Import from SRT ─────────────────────────────────────────────────
        box = layout.box()
        box.label(text="Import from SRT:", icon='IMPORT')
        box.prop(p, "srt_path", text="")
        row = box.row(align=True)
        row.operator("lvb.load_srt", icon='IMPORT')
        row.operator("lvb.save_srt", icon='EXPORT')

        # ── Style Browser (inline, collapsible) ─────────────────────────────
        box = layout.box()
        row = box.row()
        row.prop(p, "style_browser_open",
                 icon='TRIA_DOWN' if p.style_browser_open else 'TRIA_RIGHT',
                 text="Style Browser", emboss=False, toggle=True)

        if p.style_browser_open:
            if p.preview_style:
                box.label(text=f"Previewing: {p.preview_style} — click to dismiss",
                          icon='PLAY')
            if p.lines and 0 <= p.line_index < len(p.lines):
                txt   = p.lines[p.line_index].text
                label = (txt[:24] + "…") if len(txt) > 24 else txt
                box.label(text=f'Insert into: "{label}"', icon='INFO')
            else:
                box.label(text="Select a line below to use Insert", icon='INFO')

            grid = box.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
            for style_key, style_name, _ in STYLES:
                row2      = grid.row(align=True)
                is_active = (p.preview_style == style_key)
                op        = row2.operator("lvb.toggle_preview", text=style_name, depress=is_active)
                op.style  = style_key
                op2       = row2.operator("lvb.set_line_style", text="", icon='ADD')
                op2.style = style_key

        layout.separator()

        # ── Lyric list ──────────────────────────────────────────────────────
        row = layout.row()
        row.scale_y = 0.55
        split  = row.split(factor=0.40)
        split.label(text="Start → End")
        split2 = split.split(factor=0.30)
        split2.label(text="Style")
        split2.label(text="Lyric")

        layout.template_list("LVB_UL_lines", "", p, "lines", p, "line_index", rows=6)

        row = layout.row(align=True)
        row.operator("lvb.line_add",    text="", icon='ADD')
        row.separator()
        op = row.operator("lvb.line_move", text="", icon='TRIA_UP');   op.direction = 'UP'
        op = row.operator("lvb.line_move", text="", icon='TRIA_DOWN'); op.direction = 'DOWN'
        row.separator()
        row.operator("lvb.line_remove", text="Clear Line")
        row.operator("lvb.lines_clear", text="Clear All")

        layout.separator()

        # ── Edit selected line ──────────────────────────────────────────────
        if p.lines and 0 <= p.line_index < len(p.lines):
            line = p.lines[p.line_index]
            box  = layout.box()
            box.label(text=f"Line {p.line_index + 1}:", icon='GREASEPENCIL')
            row = box.row(align=True)
            row.prop(line, "time_str", text="Start")
            row.prop(line, "end_str",  text="End")
            box.prop(line, "style")
            box.prop(line, "text")

        layout.separator()

        # ── Appearance ──────────────────────────────────────────────────────
        col = layout.column(align=True)
        col.prop(p, "font_path")
        col.prop(p, "style")
        col.prop(p, "align")
        col.prop(p, "font_size")
        layout.separator()
        layout.prop(p, "text_color")
        layout.prop(p, "bg_color")
        layout.prop(p, "emission_strength")
        layout.operator("lvb.sync_appearance", icon='FILE_REFRESH')

        layout.separator()

        # ── Background Box / Border ──────────────────────────────────────────
        box2 = layout.box()
        box2.prop(p, "box_enabled")
        if p.box_enabled:
            box2.prop(p, "box_fill_color")
            box2.prop(p, "box_opacity")
            row = box2.row(align=True)
            row.prop(p, "box_padding_x")
            row.prop(p, "box_padding_y")
            box2.prop(p, "box_border_enabled")
            if p.box_border_enabled:
                box2.prop(p, "box_border_color")
                box2.prop(p, "box_border_opacity")
                box2.prop(p, "box_border_width")
            box2.label(text="Padding & border width need Generate", icon='INFO')

        layout.separator()

        # ── Timing ──────────────────────────────────────────────────────────
        row = layout.row(align=True)
        row.prop(p, "hold_frames")
        row.prop(p, "transition_frames")
        layout.prop(p, "auto_spacing")

        layout.separator()

        # ── Actions ─────────────────────────────────────────────────────────
        layout.operator("lvb.generate",     icon='TEXT')
        layout.operator("lvb.setup_render", icon='OUTPUT')
        layout.operator("lvb.clear",        icon='TRASH')


classes = [LVB_PT_main]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
