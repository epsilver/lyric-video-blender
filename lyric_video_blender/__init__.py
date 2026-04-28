bl_info = {
    "name":        "Lyric Video Blender",
    "blender":     (3, 0, 0),
    "category":    "Animation",
    "version":     (1, 0, 0),
    "description": "Extract vocals, transcribe lyrics, and animate them — all inside Blender",
    "location":    "View3D > Sidebar > LyricVideo",
}

import bpy
from bpy.props import StringProperty
from bpy.types import AddonPreferences

from . import typo, operators, panels
from .typo import LVB_UL_lines  # noqa: F401 — referenced by panels via template_list


class LVBPreferences(AddonPreferences):
    bl_idname = __name__

    venv_python: StringProperty(
        name="Python Binary (venv)",
        description="Path to the Python binary that has whisperx and demucs installed",
        default="/home/alexis/dev/song2subs/venv/bin/python",
        subtype='FILE_PATH',
    )

    def draw(self, context):
        self.layout.prop(self, "venv_python")


def register():
    bpy.utils.register_class(LVBPreferences)
    typo.register()
    operators.register()
    panels.register()


def unregister():
    panels.unregister()
    operators.unregister()
    typo.unregister()
    bpy.utils.unregister_class(LVBPreferences)
