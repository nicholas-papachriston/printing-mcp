from __future__ import annotations

from pathlib import Path


def build_generated_mesh_import_script(
    *,
    model_name: str,
    version_dir: Path,
    raw_mesh_path: Path,
    target_size_mm: tuple[float, float, float],
) -> str:
    object_name = model_name
    blend_path = version_dir / f"{model_name}.blend"
    return f"""from __future__ import annotations

import bpy
from pathlib import Path

MODEL_NAME = {model_name!r}
OBJECT_NAME = {object_name!r}
RAW_MESH_PATH = Path({str(raw_mesh_path)!r})
BLEND_PATH = {str(blend_path)!r}
TARGET_SIZE_MM = {list(target_size_mm)!r}
TOLERANCE_MM = 0.5


def import_generated_mesh() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "MILLIMETERS"
    scene.unit_settings.scale_length = 0.001

    if RAW_MESH_PATH.suffix.lower() == ".stl":
        try:
            bpy.ops.wm.stl_import(filepath=str(RAW_MESH_PATH), global_scale=0.001)
        except Exception:
            bpy.ops.import_mesh.stl(filepath=str(RAW_MESH_PATH), global_scale=0.001)
    else:
        raise ValueError(f"Unsupported generated mesh format: {{RAW_MESH_PATH}}")

    imported = [obj for obj in bpy.context.selected_objects if obj.type == "MESH"]
    if len(imported) != 1:
        raise RuntimeError(f"Expected exactly one imported mesh, got {{len(imported)}}.")

    obj = imported[0]
    obj.name = OBJECT_NAME
    obj.data.name = f"{{OBJECT_NAME}}_mesh"
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    min_z = min((obj.matrix_world @ vertex.co).z for vertex in obj.data.vertices)
    obj.location.z -= min_z
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)

    actual_size_mm = [dimension * 1000 for dimension in obj.dimensions]
    for actual, target in zip(actual_size_mm, TARGET_SIZE_MM, strict=True):
        if abs(actual - target) > TOLERANCE_MM:
            raise ValueError(
                f"Imported mesh size {{actual_size_mm}} mm does not match target "
                f"{{TARGET_SIZE_MM}} mm within {{TOLERANCE_MM}} mm."
            )

    bpy.ops.wm.save_as_mainfile(filepath=BLEND_PATH)
    print("Imported generated mesh", OBJECT_NAME, "dims_mm", [round(v, 3) for v in actual_size_mm])


import_generated_mesh()
"""


def write_generated_mesh_import_script(
    *,
    model_name: str,
    version_dir: Path,
    raw_mesh_path: Path,
    target_size_mm: tuple[float, float, float],
) -> Path:
    script_path = version_dir / f"{model_name}.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        build_generated_mesh_import_script(
            model_name=model_name,
            version_dir=version_dir,
            raw_mesh_path=raw_mesh_path,
            target_size_mm=target_size_mm,
        ),
        encoding="utf-8",
    )
    return script_path
