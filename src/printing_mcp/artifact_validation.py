from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, cast

from .files import resolve_existing_file
from .types import (
    GCodeValidationOptions,
    GCodeValidationRequest,
    JsonObject,
    MeshArtifactValidationRequest,
    ToolResult,
)

TEMP_COMMAND_RE = re.compile(r"\b(M104|M109|M140|M190)\b(?P<params>[^;\n]*)")
TEMP_VALUE_RE = re.compile(r"\bS(-?\d+(?:\.\d+)?)\b")


@dataclass(frozen=True)
class ComponentCountResult:
    count: int | None
    error: str | None = None


def validate_mesh_artifact(request: MeshArtifactValidationRequest) -> ToolResult:
    """Validate an on-disk mesh artifact without requiring Blender to be running."""

    path = resolve_existing_file(request.file_path, label="file_path")
    try:
        import trimesh
    except ImportError as error:
        return ToolResult(
            ok=False,
            backend="validation",
            error_type="backend_error",
            message=f"trimesh is not installed: {error}",
            suggestions=["Run `uv sync` in printing-mcp so mesh validation dependencies install."],
        )

    try:
        loaded = trimesh.load(str(path), process=True)
        mesh = _coerce_trimesh(loaded)
    except Exception as error:
        return ToolResult(
            ok=False,
            backend="validation",
            error_type="validation_error",
            message=f"Could not load mesh artifact {path}: {error}",
            suggestions=[
                "Confirm the file is a supported mesh format such as STL, OBJ, GLB, or 3MF.",
                "If this came from generation, import it into Blender and inspect the raw mesh.",
            ],
        )

    bounds = _mesh_bounds(mesh)
    dimensions_mm = cast(list[float | None], bounds["dimensions_mm"])
    component_result = _component_count(mesh)
    component_count = component_result.count
    volume_mm3 = _safe_float(getattr(mesh, "volume", None))
    face_count = int(len(getattr(mesh, "faces", [])))
    vertex_count = int(len(getattr(mesh, "vertices", [])))
    is_empty = bool(getattr(mesh, "is_empty", False))
    is_watertight = bool(getattr(mesh, "is_watertight", False))
    is_winding_consistent = bool(getattr(mesh, "is_winding_consistent", False))

    issues: list[str] = []
    warnings: list[str] = []

    if is_empty or face_count == 0:
        issues.append("Mesh has no faces.")
    if request.require_watertight and not is_watertight:
        issues.append("Mesh is not watertight.")
    if request.require_positive_volume and (volume_mm3 is None or abs(volume_mm3) <= 0):
        issues.append("Mesh does not report a positive non-zero volume.")
    if request.require_single_component:
        if component_count is None:
            issues.append("Mesh connected component count could not be determined.")
        elif component_count != 1:
            issues.append(f"Mesh has {component_count} connected components.")
    if request.max_faces is not None and face_count > request.max_faces:
        issues.append(f"Mesh has {face_count} faces, above max_faces={request.max_faces}.")
    if not _fits_bed(dimensions_mm, request.bed_size_mm):
        issues.append(
            f"Mesh dimensions {dimensions_mm} mm exceed bed size {request.bed_size_mm} mm."
        )
    if request.require_z0:
        min_z_mm = cast(list[float | None], bounds["min_mm"])[2]
        if min_z_mm is None or abs(min_z_mm) > request.z_tolerance_mm:
            issues.append(
                f"Mesh bottom is at Z={min_z_mm} mm, outside tolerance {request.z_tolerance_mm} mm."
            )

    if not is_winding_consistent:
        warnings.append(
            "Mesh winding is not consistent; slicer repairs may change surface normals."
        )
    if component_result.error is not None:
        warnings.append(f"Could not determine connected component count: {component_result.error}")
    if component_count is not None and component_count > 1 and not request.require_single_component:
        warnings.append(f"Mesh has {component_count} components; confirm this is intentional.")

    data: JsonObject = {
        "file_path": str(path),
        "format": path.suffix.lower().lstrip("."),
        "metrics": {
            "vertices": vertex_count,
            "faces": face_count,
            "components": component_count,
            "components_error": component_result.error,
            "is_empty": is_empty,
            "is_watertight": is_watertight,
            "is_winding_consistent": is_winding_consistent,
            "euler_number": _safe_int(getattr(mesh, "euler_number", None)),
            "volume_mm3": volume_mm3,
            "area_mm2": _safe_float(getattr(mesh, "area", None)),
            **bounds,
        },
        "thresholds": cast(JsonObject, request.model_dump(mode="json")),
        "issues": issues,
        "warnings": warnings,
    }
    return ToolResult(
        ok=not issues,
        backend="validation",
        error_type=None if not issues else "validation_error",
        message=None if not issues else "Mesh artifact validation failed.",
        suggestions=[] if not issues else ["Fix the mesh in Blender before slicing or upload."],
        data=data,
    )


def validate_gcode_file(request: GCodeValidationRequest) -> ToolResult:
    """Validate a sliced G-code/BGCode file with machine and material thresholds."""

    path = resolve_existing_file(request.file_path, label="file_path")
    try:
        import gcode_lib
    except ImportError as error:
        return ToolResult(
            ok=False,
            backend="validation",
            error_type="backend_error",
            message=f"gcode-lib is not installed: {error}",
            suggestions=[
                "Run `uv sync` in printing-mcp so G-code validation dependencies install."
            ],
        )

    try:
        gcode = gcode_lib.load(str(path))
        stats = gcode_lib.compute_stats(gcode.lines)
        bounds = gcode_lib.compute_bounds(gcode.lines)
        extrusion_bounds = gcode_lib.compute_bounds(gcode.lines, extruding_only=True)
        filament_type = request.filament_type or gcode_lib.detect_filament_type(gcode.lines)
        estimate = gcode_lib.estimate_print(
            gcode.lines,
            filament_type=filament_type,
            filament_density=request.filament_density,
        )
    except Exception as error:
        return ToolResult(
            ok=False,
            backend="validation",
            error_type="validation_error",
            message=f"Could not parse G-code file {path}: {error}",
            suggestions=[
                "Confirm the file is plain .gcode or supported .bgcode.",
                "Open the same file in OrcaSlicer/Elegoo Slicer to verify it can be parsed.",
            ],
        )

    issues: list[str] = []
    warnings: list[str] = []
    temperature_report = _temperature_report(gcode.lines)
    _validate_temperatures(request, temperature_report, issues, warnings)

    if request.require_extrusion and getattr(stats, "extrude_count", 0) <= 0:
        issues.append("G-code has no extrusion moves.")

    bounds_data = _json_value(bounds)
    extrusion_bounds_data = _json_value(extrusion_bounds)
    z_max = _safe_float(getattr(bounds, "z_max", None))
    max_z = request.max_z_mm or request.bed_size_mm[2]
    if z_max is not None and z_max > max_z:
        issues.append(f"G-code Z max {z_max} mm exceeds limit {max_z} mm.")

    oob_hits = []
    if request.check_bed_bounds:
        bed_polygon = [
            (0.0, 0.0),
            (request.bed_size_mm[0], 0.0),
            (request.bed_size_mm[0], request.bed_size_mm[1]),
            (0.0, request.bed_size_mm[1]),
        ]
        try:
            oob_hits = gcode_lib.find_oob_moves(gcode.lines, bed_polygon)
        except Exception as error:
            warnings.append(f"Could not run bed bounds check: {error}")
        if oob_hits:
            issues.append(f"Detected {len(oob_hits)} XY moves outside the configured bed bounds.")

    estimate_data = _json_value(estimate)
    print_time_seconds = _safe_float(getattr(estimate, "time_seconds", None))
    filament_weight_g = _safe_float(getattr(estimate, "filament_weight_g", None))
    if (
        request.max_print_time_seconds is not None
        and print_time_seconds is not None
        and print_time_seconds > request.max_print_time_seconds
    ):
        issues.append(
            f"Estimated print time {print_time_seconds} s exceeds "
            f"{request.max_print_time_seconds} s."
        )
    if (
        request.max_filament_weight_g is not None
        and filament_weight_g is not None
        and filament_weight_g > request.max_filament_weight_g
    ):
        issues.append(
            f"Estimated filament {filament_weight_g} g exceeds {request.max_filament_weight_g} g."
        )

    data: JsonObject = {
        "file_path": str(path),
        "source_format": gcode.source_format,
        "stats": _json_value(stats),
        "bounds": cast(JsonObject, bounds_data),
        "extrusion_bounds": cast(JsonObject, extrusion_bounds_data),
        "estimate": cast(JsonObject, estimate_data),
        "filament_type": filament_type,
        "temperatures": temperature_report,
        "oob": {
            "count": len(oob_hits),
            "sample": [_json_value(hit) for hit in oob_hits[: request.max_oob_hits_reported]],
        },
        "thresholds": cast(JsonObject, request.model_dump(mode="json")),
        "issues": issues,
        "warnings": warnings,
    }
    return ToolResult(
        ok=not issues,
        backend="validation",
        error_type=None if not issues else "validation_error",
        message=None if not issues else "G-code validation failed.",
        suggestions=[] if not issues else _gcode_suggestions(request),
        data=data,
    )


def validate_gcode_path(path: str, options: GCodeValidationOptions) -> ToolResult:
    """Apply reusable G-code options to a generated file path."""

    payload = options.model_dump(mode="json")
    payload["file_path"] = path
    return validate_gcode_file(GCodeValidationRequest.model_validate(payload))


def _coerce_trimesh(loaded: Any) -> Any:
    if hasattr(loaded, "dump") and not hasattr(loaded, "faces"):
        dumped = loaded.dump(concatenate=True)
        if isinstance(dumped, list):
            if not dumped:
                raise ValueError("Scene did not contain mesh geometry.")
            import trimesh

            return trimesh.util.concatenate(dumped)
        return dumped
    if hasattr(loaded, "faces") and hasattr(loaded, "vertices"):
        return loaded
    raise ValueError(f"Unsupported mesh object returned by loader: {type(loaded).__name__}")


def _mesh_bounds(mesh: Any) -> JsonObject:
    raw_bounds = getattr(mesh, "bounds", None)
    if raw_bounds is None or len(raw_bounds) != 2:
        return {
            "min_mm": [None, None, None],
            "max_mm": [None, None, None],
            "dimensions_mm": [None, None, None],
        }
    min_mm = [_safe_float(value) for value in raw_bounds[0]]
    max_mm = [_safe_float(value) for value in raw_bounds[1]]
    dimensions = [
        round(max_value - min_value, 4) if min_value is not None and max_value is not None else None
        for min_value, max_value in zip(min_mm, max_mm, strict=True)
    ]
    return {
        "min_mm": [round(value, 4) if value is not None else None for value in min_mm],
        "max_mm": [round(value, 4) if value is not None else None for value in max_mm],
        "dimensions_mm": dimensions,
    }


def _component_count(mesh: Any) -> ComponentCountResult:
    try:
        return ComponentCountResult(count=len(mesh.split(only_watertight=False)))
    except Exception as error:
        return ComponentCountResult(count=None, error=str(error))


def _fits_bed(dimensions_mm: list[float | None], bed_size_mm: tuple[float, float, float]) -> bool:
    for value, limit in zip(dimensions_mm, bed_size_mm, strict=True):
        if value is None or value > limit:
            return False
    return True


def _temperature_report(lines: list[Any]) -> JsonObject:
    commands: list[JsonObject] = []
    first_nozzle_wait: JsonObject | None = None
    first_bed_wait: JsonObject | None = None
    max_nozzle: float | None = None
    max_bed: float | None = None

    for line_number, line in enumerate(lines, start=1):
        raw = getattr(line, "raw", "")
        for command_match in TEMP_COMMAND_RE.finditer(raw):
            value_match = TEMP_VALUE_RE.search(command_match.group("params"))
            if value_match is None:
                continue
            temp_c = float(value_match.group(1))
            command = command_match.group(1)
            item: JsonObject = {
                "line": line_number,
                "command": command,
                "temp_c": temp_c,
                "raw": raw.strip(),
            }
            commands.append(item)
            if command in {"M104", "M109"}:
                max_nozzle = temp_c if max_nozzle is None else max(max_nozzle, temp_c)
                if command == "M109" and first_nozzle_wait is None and temp_c > 0:
                    first_nozzle_wait = item
            if command in {"M140", "M190"}:
                max_bed = temp_c if max_bed is None else max(max_bed, temp_c)
                if command == "M190" and first_bed_wait is None and temp_c > 0:
                    first_bed_wait = item

    return {
        "commands": commands[:100],
        "command_count": len(commands),
        "first_nozzle_wait": first_nozzle_wait,
        "first_bed_wait": first_bed_wait,
        "max_nozzle_temp_c": max_nozzle,
        "max_bed_temp_c": max_bed,
    }


def _validate_temperatures(
    request: GCodeValidationRequest,
    report: JsonObject,
    issues: list[str],
    warnings: list[str],
) -> None:
    max_nozzle = report.get("max_nozzle_temp_c")
    max_bed = report.get("max_bed_temp_c")
    if isinstance(max_nozzle, int | float) and max_nozzle > request.max_nozzle_temp_c:
        issues.append(f"Nozzle temperature {max_nozzle} C exceeds {request.max_nozzle_temp_c} C.")
    if isinstance(max_bed, int | float) and max_bed > request.max_bed_temp_c:
        issues.append(f"Bed temperature {max_bed} C exceeds {request.max_bed_temp_c} C.")

    first_nozzle_wait = report.get("first_nozzle_wait")
    first_bed_wait = report.get("first_bed_wait")
    _validate_wait_temperature(
        first_nozzle_wait,
        label="nozzle wait",
        minimum=request.min_wait_nozzle_temp_c,
        maximum=request.max_wait_nozzle_temp_c,
        issues=issues,
        warnings=warnings,
    )
    _validate_wait_temperature(
        first_bed_wait,
        label="bed wait",
        minimum=request.min_wait_bed_temp_c,
        maximum=request.max_wait_bed_temp_c,
        issues=issues,
        warnings=warnings,
    )


def _validate_wait_temperature(
    item: object,
    *,
    label: str,
    minimum: float | None,
    maximum: float | None,
    issues: list[str],
    warnings: list[str],
) -> None:
    if minimum is None and maximum is None:
        return
    if not isinstance(item, dict):
        warnings.append(f"No {label} command was found to compare against the requested range.")
        return
    wait_item = cast(dict[str, Any], item)
    temp_c = wait_item.get("temp_c")
    if not isinstance(temp_c, int | float):
        warnings.append(f"No numeric {label} temperature was found.")
        return
    if minimum is not None and temp_c < minimum:
        issues.append(f"First {label} temperature {temp_c} C is below {minimum} C.")
    if maximum is not None and temp_c > maximum:
        issues.append(f"First {label} temperature {temp_c} C is above {maximum} C.")


def _gcode_suggestions(request: GCodeValidationRequest) -> list[str]:
    suggestions = [
        "Inspect the slicer preview and G-code start block before uploading to the printer.",
        "If material/time is too high, revise shell geometry before tuning slicer speed.",
    ]
    if request.min_wait_bed_temp_c is not None or request.max_wait_bed_temp_c is not None:
        suggestions.append(
            "For CC2 PLA slices, verify process JSON sets cool/hot/textured plate temperatures."
        )
    return suggestions


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else None
    return value


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, 6)


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
