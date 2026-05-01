from __future__ import annotations

from importlib.util import find_spec
from typing import cast

from .types import JsonObject, ToolResult

BLENDER_HELPER_SNIPPET = r"""from __future__ import annotations

import math
from pathlib import Path

import bpy

M = 0.001


def mm(value: float) -> float:
    return value * M


def setup_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False, confirm=False)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "MILLIMETERS"
    scene.unit_settings.scale_length = 0.001


def material(name: str, color: tuple[float, float, float, float]) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    return mat


def activate(obj: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def apply_modifier(obj: bpy.types.Object, name: str) -> None:
    activate(obj)
    bpy.ops.object.modifier_apply(modifier=name)


def add_cube(
    name: str,
    location_mm: tuple[float, float, float],
    dimensions_mm: tuple[float, float, float],
    mat: bpy.types.Material,
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=tuple(mm(value) for value in location_mm))
    obj = bpy.context.active_object
    obj.name = name
    obj.dimensions = tuple(mm(value) for value in dimensions_mm)
    obj.data.materials.append(mat)
    activate(obj)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return obj


def add_cylinder(
    name: str,
    radius_mm: float,
    depth_mm: float,
    location_mm: tuple[float, float, float],
    mat: bpy.types.Material,
    *,
    vertices: int = 64,
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=vertices,
        radius=mm(radius_mm),
        depth=mm(depth_mm),
        location=tuple(mm(value) for value in location_mm),
    )
    obj = bpy.context.active_object
    obj.name = name
    obj.data.materials.append(mat)
    return obj


def add_uv_sphere(
    name: str,
    location_mm: tuple[float, float, float],
    scale_mm: tuple[float, float, float],
    mat: bpy.types.Material,
    *,
    segments: int = 64,
    rings: int = 24,
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=segments,
        ring_count=rings,
        location=tuple(mm(value) for value in location_mm),
    )
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = tuple(mm(value) for value in scale_mm)
    obj.data.materials.append(mat)
    activate(obj)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return obj


def boolean(target: bpy.types.Object, cutter: bpy.types.Object, operation: str) -> bpy.types.Object:
    activate(target)
    mod = target.modifiers.new(f"{operation.lower()}_{cutter.name}", type="BOOLEAN")
    mod.operation = operation
    mod.object = cutter
    mod.solver = "EXACT"
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(cutter, do_unlink=True)
    return target


def union(target: bpy.types.Object, cutter: bpy.types.Object) -> bpy.types.Object:
    return boolean(target, cutter, "UNION")


def difference(target: bpy.types.Object, cutter: bpy.types.Object) -> bpy.types.Object:
    return boolean(target, cutter, "DIFFERENCE")


def drop_to_z0(obj: bpy.types.Object) -> None:
    activate(obj)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    min_z = min((obj.matrix_world @ vertex.co).z for vertex in obj.data.vertices)
    obj.location.z -= min_z
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)


def export_stl_mm(obj: bpy.types.Object, target_path: Path) -> None:
    activate(obj)
    try:
        bpy.ops.wm.stl_export(
            filepath=str(target_path),
            export_selected_objects=True,
            global_scale=1000.0,
            apply_modifiers=True,
            ascii_format=False,
        )
    except Exception:
        bpy.ops.export_mesh.stl(
            filepath=str(target_path),
            use_selection=True,
            global_scale=1000.0,
            ascii=False,
            use_mesh_modifiers=True,
        )
"""

BUILD123D_STARTER_SNIPPET = r"""from __future__ import annotations

from build123d import Align, Box, BuildPart, Cylinder, Mode, Pos, export_stl


def build_part():
    with BuildPart() as part:
        Box(80, 60, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Pos(0, 0, 6):
            Cylinder(radius=24, height=28, mode=Mode.ADD)
        with Pos(0, -18, 6):
            Box(34, 18, 18, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
    return part.part


if __name__ == "__main__":
    export_stl(build_part(), "part.stl")
"""


def get_authoring_capabilities() -> ToolResult:
    """Return agent-facing geometry authoring options and reusable snippets."""

    libraries: JsonObject = {
        "build123d": {
            "available": find_spec("build123d") is not None,
            "best_for": "Dimensioned parametric CAD, sketches, extrusions, fillets, lofts.",
        },
        "trimesh": {
            "available": find_spec("trimesh") is not None,
            "best_for": "Standalone STL/mesh metrics and validation before or after Blender.",
        },
        "gcode-lib": {
            "available": find_spec("gcode_lib") is not None,
            "best_for": "Post-slice G-code parsing, bounds checks, layers, and statistics.",
        },
    }
    payload: JsonObject = {
        "routes": [
            {
                "route": "blender_helpers",
                "use_when": (
                    "Organic, decorative, or mesh-first printable models that still need "
                    "Blender MCP visual review and STL export."
                ),
            },
            {
                "route": "build123d",
                "use_when": (
                    "Dimensioned parts where sketches, BREP booleans, fillets, and STEP/STL "
                    "exports are more reliable than hand-built mesh primitives."
                ),
            },
            {
                "route": "fusion360",
                "use_when": (
                    "The user explicitly asks for Fusion/Autodesk or the part needs editable "
                    "CAD history, constraints, threads, or assemblies."
                ),
            },
        ],
        "libraries": libraries,
        "snippets": {
            "blender_helper_snippet": BLENDER_HELPER_SNIPPET,
            "build123d_starter_snippet": BUILD123D_STARTER_SNIPPET,
        },
        "workflow": [
            "Keep generated or authored model scripts file-backed in <model>/v<N>/<model>.py.",
            (
                "Use build123d for CAD-grade source geometry, then import/export through "
                "Blender when visual review or repo-standard STL handoff is required."
            ),
            (
                "Run mesh_validate_artifact on STL/GLB/OBJ artifacts and "
                "gcode_validate_file after slicing."
            ),
        ],
    }
    return ToolResult(ok=True, backend="authoring", data=cast(JsonObject, payload))
