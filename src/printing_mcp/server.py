from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TypeVar, cast

from mcp.server.fastmcp import FastMCP, Image
from pydantic import BaseModel, ValidationError

from .artifact_validation import validate_gcode_file, validate_mesh_artifact
from .authoring import get_authoring_capabilities
from .blender import BlenderRunner
from .config import (
    load_local_env,
    load_printing_config,
    loaded_env_path,
    modal_auth_configured,
)
from .elegoo import ElegooLanRunner
from .files import resolve_output_dir, resolve_parent_for_output
from .generation.backend_client import GenerationBackendClient
from .generation.background import GenerationBackgroundRunner
from .generation.dependencies import GenerationDependencies
from .generation.image import GenerationImageRunner
from .generation.jobs import GenerationJobStore
from .generation.mesh import GenerationMeshRunner
from .generation.schemas import (
    GenerationCancelJobRequest,
    GenerationCreateImageRequest,
    GenerationCreateMeshRequest,
    GenerationGetJobRequest,
    GenerationRemoveBackgroundRequest,
    GenerationWarmBackendRequest,
)
from .orca import OrcaRunner
from .types import (
    BackendStatus,
    BlenderBootstrapModelRequest,
    BlenderCodeRequest,
    BlenderExportStlRequest,
    BlenderPrintabilityReviewRequest,
    BlenderValidateObjectRequest,
    BlenderVisualReviewRequest,
    CommandResult,
    ElegooDiscoveryRequest,
    ElegooPrinterRequest,
    ElegooStartPrintRequest,
    ElegooUploadAndStartRequest,
    ElegooUploadRequest,
    Export3mfRequest,
    ExportSettingsRequest,
    ExportStlRequest,
    GCodeValidationRequest,
    InspectModelRequest,
    JsonObject,
    MeshArtifactValidationRequest,
    ObjectInfoRequest,
    SliceRequest,
    ToolResult,
    ViewportScreenshotRequest,
)

T = TypeVar("T", bound=BaseModel)
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
load_local_env()

mcp = FastMCP("printing")
blender_runner = BlenderRunner()
elegoo_runner = ElegooLanRunner()
generation_background_runner = GenerationBackgroundRunner(WORKSPACE_ROOT)
generation_backend_client = GenerationBackendClient()
generation_job_store = GenerationJobStore()
generation_dependencies = GenerationDependencies(
    workspace_root=WORKSPACE_ROOT,
    backend_client=generation_backend_client,
    job_store=generation_job_store,
)
generation_image_runner = GenerationImageRunner(generation_dependencies)
generation_mesh_runner = GenerationMeshRunner(dependencies=generation_dependencies)
orca_runner = OrcaRunner()
last_result: ToolResult | CommandResult | None = None


def _coerce(model_type: type[T], value: T | JsonObject) -> T:
    if isinstance(value, model_type):
        return value
    return model_type.model_validate(value)


def _record(result: ToolResult | CommandResult) -> JsonObject:
    global last_result
    last_result = result
    return _dump_model(result)


def _run_tool(callback: Callable[[], ToolResult | CommandResult]) -> JsonObject:
    try:
        return _record(callback())
    except ValueError as error:
        return _record(
            ToolResult(
                ok=False,
                error_type="invalid_request",
                suggestions=[
                    "Check required fields, paths, object names, and numeric limits.",
                    "Use printing_status to confirm available backends before retrying.",
                ],
                message=str(error),
            )
        )
    except ValidationError as error:
        return _record(
            ToolResult(
                ok=False,
                error_type="validation_error",
                suggestions=[
                    "Check the request schema, required fields, and field types.",
                    "For orcaslicer_slice_model, provide model_paths and output_dir.",
                    "For Blender tools, provide non-empty code or object names.",
                ],
                message=_format_validation_error(error),
            )
        )


@mcp.tool()
def printing_status() -> JsonObject:
    """Check all Printing MCP backend availability and resolved configuration.

    Use this before backend-specific tools to confirm Blender, Elegoo LAN, and
    OrcaSlicer are reachable. Example: call with no arguments.
    """

    config = load_printing_config()
    blender_runner.reload_config()
    elegoo_runner.reload_config()
    orca_runner.reload_config()

    blender_result = blender_runner.status()
    elegoo_result = (
        elegoo_runner.status(ElegooPrinterRequest())
        if config.elegoo.host
        else ToolResult(
            ok=False,
            backend="elegoo",
            error_type="invalid_request",
            message="No Elegoo LAN printer host configured.",
            suggestions=[
                "Run elegoo_discover_lan_printers to find printers on the LAN.",
                "Pass host/printer_type to Elegoo tools or set ELEGOO_PRINTER_HOST.",
            ],
        )
    )
    orca_result = orca_runner.help(timeout_seconds=min(config.orca.timeout_seconds, 20.0))
    _record(
        ToolResult(
            ok=blender_result.ok and elegoo_result.ok and orca_result.ok,
            data={
                "blender": blender_result.model_dump(mode="json"),
                "elegoo": elegoo_result.model_dump(mode="json"),
                "orcaslicer": orca_result.model_dump(mode="json"),
            },
        )
    )

    help_text = orca_result.stdout or orca_result.stderr
    help_excerpt = [line for line in help_text.splitlines()[:20] if line.strip()]

    return {
        "blender": _dump_model(
            BackendStatus(
                available=blender_result.ok,
                message=blender_result.message,
                suggestions=blender_result.suggestions,
            )
        ),
        "elegoo": _dump_model(
            BackendStatus(
                available=elegoo_result.ok,
                message=elegoo_result.message,
                suggestions=elegoo_result.suggestions,
            )
        ),
        "orcaslicer": _dump_model(
            BackendStatus(
                available=orca_result.ok,
                message=orca_result.message,
                suggestions=orca_result.suggestions,
            )
        ),
        "binary_path": str(config.orca.binary_path) if config.orca.binary_path else None,
        "datadir": str(config.orca.datadir) if config.orca.datadir else None,
        "blender_host": config.blender.host,
        "blender_port": config.blender.port,
        "elegoo_host": config.elegoo.host,
        "env_file_loaded": str(loaded_env_path()) if loaded_env_path() else None,
        "modal_auth_configured": modal_auth_configured(),
        "generation_dry_run": bool(os.environ.get("PRINTING_GENERATION_DRY_RUN")),
        "timeout_seconds": max(
            config.blender.timeout_seconds,
            config.elegoo.timeout_seconds,
            config.orca.timeout_seconds,
        ),
        "help_excerpt": help_excerpt,
    }


@mcp.tool()
def blender_get_scene_info() -> JsonObject:
    """Get Blender scene objects, unit settings, and basic counts.

    Example: call with no arguments before creating or editing geometry.
    """

    return _run_tool(blender_runner.get_scene_info)


@mcp.tool()
def blender_get_capabilities() -> JsonObject:
    """Report Blender bridge capabilities and available exporters.

    Example: call with no arguments after restarting Blender to confirm
    visual_review, printability_review, and current STL export support.
    """

    return _run_tool(blender_runner.get_capabilities)


@mcp.tool()
def blender_get_object_info(request: ObjectInfoRequest) -> JsonObject:
    """Get transform, dimensions, materials, and mesh stats for one Blender object.

    Example: {"request": {"object_name": "fairy_castle_gecko_hide"}}
    """

    validated = _coerce(ObjectInfoRequest, request)
    return _run_tool(lambda: blender_runner.get_object_info(validated.object_name))


@mcp.tool()
def blender_validate_object(request: BlenderValidateObjectRequest) -> JsonObject:
    """Run hard mesh validation for one Blender object.

    Checks manifold, boundary, wire, non-contiguous, zero-area, volume, and Z
    placement metrics. Example:
    {"request": {"object_name": "final_part", "evaluated": true}}
    """

    validated = _coerce(BlenderValidateObjectRequest, request)
    return _run_tool(
        lambda: blender_runner.validate_object(
            validated.object_name,
            evaluated=validated.evaluated,
        )
    )


@mcp.tool()
def blender_review_printability(request: BlenderPrintabilityReviewRequest) -> JsonObject:
    """Review Blender geometry for softer print-readiness risks.

    Use after blender_validate_object. Flags disconnected shells, overhangs,
    tiny features, bed fit, loose geometry, and placement. Example:
    {"request": {"object_name": "final_part", "require_single_component": true}}
    """

    review = _coerce(BlenderPrintabilityReviewRequest, request)
    return _run_tool(
        lambda: blender_runner.review_printability(
            review.object_name,
            evaluated=review.evaluated,
            bed_size_mm=review.bed_size_mm,
            overhang_degrees=review.overhang_degrees,
            min_feature_mm=review.min_feature_mm,
            require_single_component=review.require_single_component,
        )
    )


@mcp.tool()
def blender_review_visuals(request: BlenderVisualReviewRequest) -> JsonObject:
    """Capture front, right, top, and isometric Blender screenshots for review.

    Use before export or print handoff to inspect silhouette, proportions, and
    whether decorative details visibly attach to the model. Save the output into
    the model version's visual_review directory. Example:
    {"request": {"object_name": "final_part", "output_dir": "/tmp/visual_review"}}
    """

    review = _coerce(BlenderVisualReviewRequest, request)
    return _run_tool(
        lambda: blender_runner.review_visuals(
            review.object_name,
            output_dir=resolve_output_dir(review.output_dir),
            max_size=review.max_size,
            include_top=review.include_top,
            include_front=review.include_front,
            include_right=review.include_right,
            include_isometric=review.include_isometric,
        )
    )


@mcp.tool()
def blender_export_stl(request: BlenderExportStlRequest) -> JsonObject:
    """Validate and export one Blender object to STL.

    Uses Blender's current STL exporter and refuses invalid meshes unless
    allow_non_manifold is explicit. Example:
    {"request": {"object_name": "final_part", "target_path": "/tmp/final_part.stl"}}
    """

    export = _coerce(BlenderExportStlRequest, request)
    return _run_tool(
        lambda: blender_runner.export_stl(
            export.object_name,
            resolve_parent_for_output(export.target_path),
            global_scale=export.global_scale,
            validate=export.validate_mesh,
            allow_non_manifold=export.allow_non_manifold,
        )
    )


@mcp.tool()
def blender_bootstrap_model(request: BlenderBootstrapModelRequest) -> JsonObject:
    """Create a repo-standard model/version folder and reset Blender scene units.

    Clears the scene, sets metric millimeter units, and returns standard output
    paths. Example:
    {"request": {"model_name": "gecko_hide", "version": 1}}
    """

    bootstrap = _coerce(BlenderBootstrapModelRequest, request)
    return _run_tool(
        lambda: blender_runner.bootstrap_model(
            bootstrap.model_name,
            version=bootstrap.version,
            output_root=(
                resolve_output_dir(bootstrap.output_root)
                if bootstrap.output_root is not None
                else WORKSPACE_ROOT
            ),
        )
    )


@mcp.tool()
def blender_execute_code(request: BlenderCodeRequest) -> JsonObject:
    """Execute Python code in Blender through the local bridge.

    For geometry creation or edits, write the code to the model version's
    `<model>.py` file first, then pass that file's contents here. When
    iterating, edit the file before executing again. For scripts guarded by
    `if __name__ == "__main__":`, execute with a namespace that sets
    `__name__` to `"__main__"` and `__file__` to the script path.
    """

    validated = _coerce(BlenderCodeRequest, request)
    return _run_tool(
        lambda: blender_runner.execute_code(
            validated.code,
            timeout_seconds=validated.timeout_seconds,
        )
    )


@mcp.tool()
def blender_get_viewport_screenshot(request: ViewportScreenshotRequest | None = None) -> object:
    """Return the current Blender 3D viewport as a PNG image.

    For multi-angle saved screenshots, prefer blender_review_visuals. Example:
    {"request": {"max_size": 1200}}
    """

    validated = (
        ViewportScreenshotRequest()
        if request is None
        else _coerce(ViewportScreenshotRequest, request)
    )

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
        screenshot_path = Path(temp_file.name)

    try:
        result = blender_runner.capture_viewport_screenshot(
            screenshot_path,
            max_size=validated.max_size,
        )
        if not result.ok:
            return _record(result)
        if not screenshot_path.is_file():
            return _record(
                ToolResult(
                    ok=False,
                    backend="blender",
                    error_type="backend_error",
                    message="Blender reported screenshot success but did not create the PNG file.",
                    suggestions=[
                        "Restart the Blender addon and retry blender_get_viewport_screenshot."
                    ],
                )
            )
        image_bytes = screenshot_path.read_bytes()
        _record(result)
        return Image(data=image_bytes, format="png")
    finally:
        with suppress(OSError):
            screenshot_path.unlink(missing_ok=True)


@mcp.tool()
def geometry_authoring_capabilities() -> JsonObject:
    """Return recommended geometry authoring routes and reusable helper snippets.

    Use before creating complex shapes to choose between Blender helpers,
    build123d parametric CAD, and Fusion 360.
    """

    return _run_tool(get_authoring_capabilities)


@mcp.tool()
def mesh_validate_artifact(request: MeshArtifactValidationRequest) -> JsonObject:
    """Validate an on-disk STL/OBJ/GLB/3MF mesh artifact without Blender.

    This is a fast artifact gate for generated or exported meshes. Blender still
    owns final cleanup, visual review, and canonical STL export.
    """

    return _run_tool(
        lambda: validate_mesh_artifact(_coerce(MeshArtifactValidationRequest, request))
    )


@mcp.tool()
def gcode_validate_file(request: GCodeValidationRequest) -> JsonObject:
    """Validate a sliced G-code/BGCode file before upload or print start.

    Checks parsed moves, bounds, extrusion presence, temperature limits, and
    optional material/time thresholds.
    """

    return _run_tool(lambda: validate_gcode_file(_coerce(GCodeValidationRequest, request)))


@mcp.tool()
def generation_remove_background(request: GenerationRemoveBackgroundRequest) -> JsonObject:
    """Create a local foreground image handoff for the generation pipeline.

    This deterministic local step currently validates the image type, copies the
    source image to output_path, and appends a generation.json call entry. Example:
    {"request": {"model_name": "generation_smoke", "version": 1, "source_path": "..."}}
    """

    validated = _coerce(GenerationRemoveBackgroundRequest, request)
    return _run_tool(lambda: generation_background_runner.remove_background(validated))


@mcp.tool()
def generation_warm_backend(request: GenerationWarmBackendRequest) -> JsonObject:
    """Acknowledge and record an explicit billable backend warmup request.

    The current implementation validates provider/cost confirmation and records
    a completed warmup acknowledgement. Real Modal cache hydration still happens
    on the first generation call.
    """

    validated = _coerce(GenerationWarmBackendRequest, request)
    return _run_tool(lambda: generation_mesh_runner.warm_backend(validated))


@mcp.tool()
def generation_create_image(request: GenerationCreateImageRequest) -> JsonObject:
    """Create a source image from a prompt for the generation pipeline.

    Uses Nano Banana 2 through Google GenAI when GEMINI_API_KEY/GOOGLE_API_KEY is
    configured. Placeholder image output requires PRINTING_GENERATION_DRY_RUN=1.
    The call is synchronous and returns a completed job with provenance recorded.
    """

    validated = _coerce(GenerationCreateImageRequest, request)
    return _run_tool(lambda: generation_image_runner.create_image(validated))


@mcp.tool()
def generation_create_mesh(request: GenerationCreateMeshRequest) -> JsonObject:
    """Create a scaled raw mesh artifact from a source image.

    Uses Modal TripoSR for provider='modal', quality='fast', mesh_model='triposr'.
    When PRINTING_GENERATION_DRY_RUN is set, writes placeholder raw STL/GLB
    artifacts instead. The call is synchronous and returns a completed job.
    """

    validated = _coerce(GenerationCreateMeshRequest, request)
    return _run_tool(lambda: generation_mesh_runner.create_mesh(validated))


@mcp.tool()
def generation_get_job(request: GenerationGetJobRequest) -> JsonObject:
    """Return a stored terminal generation job result by compatibility job ID."""

    validated = _coerce(GenerationGetJobRequest, request)
    return _run_tool(lambda: generation_mesh_runner.get_job(validated))


@mcp.tool()
def generation_cancel_job(request: GenerationCancelJobRequest) -> JsonObject:
    """Report that returned synchronous generation jobs are not cancellable."""

    validated = _coerce(GenerationCancelJobRequest, request)
    return _run_tool(lambda: generation_mesh_runner.cancel_job(validated))


@mcp.tool()
def elegoo_discover_lan_printers(request: ElegooDiscoveryRequest | None = None) -> JsonObject:
    """Discover Elegoo printers on the local network using LAN UDP only.

    Example: {"request": {"timeout_seconds": 8, "include_cc2": true, "include_cc": true}}
    """

    validated = (
        ElegooDiscoveryRequest() if request is None else _coerce(ElegooDiscoveryRequest, request)
    )
    return _run_tool(
        lambda: elegoo_runner.discover(
            validated.timeout_seconds,
            include_cc2=validated.include_cc2,
            include_cc=validated.include_cc,
        )
    )


@mcp.tool()
def elegoo_get_status(request: ElegooPrinterRequest) -> JsonObject:
    """Get Elegoo LAN printer status using CC2 MQTT or Centauri WebSocket.

    Example: {"request": {"host": "192.168.1.50", "printer_type": "cc2"}}
    """

    return _run_tool(lambda: elegoo_runner.status(_coerce(ElegooPrinterRequest, request)))


@mcp.tool()
def elegoo_upload_file(request: ElegooUploadRequest) -> JsonObject:
    """Upload a local G-code or 3MF file to an Elegoo LAN printer.

    Does not use cloud staging. Example:
    {"request": {"host": "192.168.1.50", "printer_type": "cc2", "file_path": "/tmp/model.gcode"}}
    """

    return _run_tool(lambda: elegoo_runner.upload(_coerce(ElegooUploadRequest, request)))


@mcp.tool()
def elegoo_start_print(request: ElegooStartPrintRequest) -> JsonObject:
    """Start a file that already exists on an Elegoo LAN printer.

    Example:
    {"request": {"host": "192.168.1.50", "printer_type": "cc2", "file_name": "model.gcode"}}
    """

    return _run_tool(lambda: elegoo_runner.start_print(_coerce(ElegooStartPrintRequest, request)))


@mcp.tool()
def elegoo_upload_and_start_print(request: ElegooUploadAndStartRequest) -> JsonObject:
    """Upload a local file to an Elegoo LAN printer and start it.

    Example:
    {"request": {"host": "192.168.1.50", "printer_type": "cc2", "file_path": "/tmp/model.gcode"}}
    """

    return _run_tool(
        lambda: elegoo_runner.upload_and_start_print(_coerce(ElegooUploadAndStartRequest, request))
    )


@mcp.tool()
def elegoo_pause_print(request: ElegooPrinterRequest) -> JsonObject:
    """Pause the active Elegoo LAN print.

    Example: {"request": {"host": "192.168.1.50", "printer_type": "cc2"}}
    """

    return _run_tool(lambda: elegoo_runner.pause_print(_coerce(ElegooPrinterRequest, request)))


@mcp.tool()
def elegoo_resume_print(request: ElegooPrinterRequest) -> JsonObject:
    """Resume the active paused Elegoo LAN print.

    Example: {"request": {"host": "192.168.1.50", "printer_type": "cc2"}}
    """

    return _run_tool(lambda: elegoo_runner.resume_print(_coerce(ElegooPrinterRequest, request)))


@mcp.tool()
def elegoo_cancel_print(request: ElegooPrinterRequest) -> JsonObject:
    """Cancel the active Elegoo LAN print and return the printer toward idle.

    Example: {"request": {"host": "192.168.1.50", "printer_type": "cc2"}}
    """

    return _run_tool(lambda: elegoo_runner.cancel_print(_coerce(ElegooPrinterRequest, request)))


@mcp.tool()
def elegoo_stop_print(request: ElegooPrinterRequest) -> JsonObject:
    """Stop the active Elegoo LAN print.

    Example: {"request": {"host": "192.168.1.50", "printer_type": "cc2"}}
    """

    return _run_tool(lambda: elegoo_runner.stop_print(_coerce(ElegooPrinterRequest, request)))


@mcp.tool()
def elegoo_load_filament(request: ElegooPrinterRequest) -> JsonObject:
    """Start the printer's built-in filament load routine over Elegoo LAN.

    Uses the firmware load command surfaced in Elegoo's LAN adapters. Example:
    {"request": {"host": "192.168.1.50", "printer_type": "cc2"}}
    """

    return _run_tool(lambda: elegoo_runner.load_filament(_coerce(ElegooPrinterRequest, request)))


@mcp.tool()
def elegoo_unload_filament(request: ElegooPrinterRequest) -> JsonObject:
    """Start the printer's built-in filament unload routine over Elegoo LAN.

    Uses the firmware unload command surfaced in Elegoo's LAN adapters. Example:
    {"request": {"host": "192.168.1.50", "printer_type": "cc2"}}
    """

    return _run_tool(lambda: elegoo_runner.unload_filament(_coerce(ElegooPrinterRequest, request)))


@mcp.tool()
def orcaslicer_help(include_print_options: bool = False) -> JsonObject:
    """Return OrcaSlicer command-line help output.

    Example: {"include_print_options": false}
    """

    return _run_tool(lambda: orca_runner.help(include_print_options=include_print_options))


@mcp.tool()
def orcaslicer_slice_model(request: SliceRequest) -> JsonObject:
    """Slice one or more model files with OrcaSlicer and return generated files.

    If validate_gcode is true, validates discovered .gcode/.bgcode outputs and
    marks the command failed when post-slice validation fails.
    Example:
    {"request": {"model_paths": ["/tmp/model.stl"], "output_dir": "/tmp/out"}}
    """

    return _run_tool(lambda: orca_runner.slice_model(_coerce(SliceRequest, request)))


@mcp.tool()
def orcaslicer_export_3mf(request: Export3mfRequest) -> JsonObject:
    """Export one or more models as an OrcaSlicer 3MF project.

    Example:
    {"request": {"model_paths": ["/tmp/model.stl"], "target_path": "/tmp/model.3mf"}}
    """

    return _run_tool(lambda: orca_runner.export_3mf(_coerce(Export3mfRequest, request)))


@mcp.tool()
def orcaslicer_export_stl(request: ExportStlRequest) -> JsonObject:
    """Export model files as STL through OrcaSlicer.

    Use blender_export_stl for exporting the active Blender object. Example:
    {"request": {"model_paths": ["/tmp/model.3mf"], "mode": "single"}}
    """

    return _run_tool(lambda: orca_runner.export_stl(_coerce(ExportStlRequest, request)))


@mcp.tool()
def orcaslicer_inspect_model(request: InspectModelRequest) -> JsonObject:
    """Return OrcaSlicer model information for one or more files.

    Example: {"request": {"model_paths": ["/tmp/model.stl"]}}
    """

    return _run_tool(lambda: orca_runner.inspect_model(_coerce(InspectModelRequest, request)))


@mcp.tool()
def orcaslicer_export_settings(request: ExportSettingsRequest) -> JsonObject:
    """Export resolved OrcaSlicer settings to a JSON file.

    Example: {"request": {"target_path": "/tmp/settings.json", "model_paths": ["/tmp/model.stl"]}}
    """

    return _run_tool(lambda: orca_runner.export_settings(_coerce(ExportSettingsRequest, request)))


@mcp.resource("printing://config")
def config_resource() -> str:
    """Return resolved Printing MCP configuration without secrets."""

    config = load_printing_config()
    payload: JsonObject = {
        "blender": {
            "host": config.blender.host,
            "port": config.blender.port,
            "timeout_seconds": config.blender.timeout_seconds,
            "config_issue": config.blender.config_issue,
        },
        "elegoo": {
            "host": config.elegoo.host,
            "serial_number": config.elegoo.serial_number,
            "mainboard_id": config.elegoo.mainboard_id,
            "printer_type": config.elegoo.printer_type,
            "timeout_seconds": config.elegoo.timeout_seconds,
            "discovery_timeout_seconds": config.elegoo.discovery_timeout_seconds,
            "upload_chunk_bytes": config.elegoo.upload_chunk_bytes,
            "access_code_configured": config.elegoo.access_code is not None,
            "default_access_code_enabled": config.elegoo.use_default_access_code,
            "config_issue": config.elegoo.config_issue,
        },
        "orcaslicer": {
            "available": config.orca.available,
            "binary_path": str(config.orca.binary_path) if config.orca.binary_path else None,
            "datadir": str(config.orca.datadir) if config.orca.datadir else None,
            "timeout_seconds": config.orca.timeout_seconds,
            "binary_issue": config.orca.binary_issue,
        },
        "generation": {
            "env_file_loaded": str(loaded_env_path()) if loaded_env_path() else None,
            "modal_auth_configured": modal_auth_configured(),
            "dry_run": bool(os.environ.get("PRINTING_GENERATION_DRY_RUN")),
        },
    }
    return json.dumps(payload, indent=2)


@mcp.resource("printing://last-run")
def last_run_resource() -> str:
    """Return the most recent backend command result observed by this server."""

    if last_result is None:
        return json.dumps({"message": "No Printing MCP command has run yet."}, indent=2)
    return last_result.model_dump_json(indent=2)


@mcp.resource("printing://workflow")
def workflow_resource() -> str:
    """Return this repo's core modeling and slicing workflow constraints."""

    payload: JsonObject = {
        "model_layout": "<model>/v<N>/<model>.py, .blend, .stl, optional .3mf/.gcode",
        "units": (
            "Millimeters conceptually; Blender coordinates use meters with scale_length=0.001."
        ),
        "orientation": "+Z up, printable bottom on Z=0, origin near footprint center.",
        "validation": (
            "Run blender_validate_object before export; manifold meshes with positive volume "
            "are the expected input for reliable Booleans and slicing."
        ),
        "artifact_validation": (
            "Use mesh_validate_artifact for standalone STL/OBJ/GLB/3MF checks before slicing "
            "or when Blender is not running."
        ),
        "printability_review": (
            "Run blender_review_printability after hard validation to catch disconnected shells, "
            "overhangs, small-feature candidates, bed-fit problems, and placement issues."
        ),
        "visual_review": (
            "Run blender_review_visuals before export or print handoff; inspect the saved "
            "front, side, top, and isometric screenshots for silhouette and proportion issues."
        ),
        "stl_export": (
            "Use blender_export_stl for validated Blender exports; it calls Blender's current "
            "STL operator with global_scale=1000.0."
        ),
        "generation": (
            "Generated mesh versions keep provenance in generation/generation.json. Raw generated "
            "meshes must be imported into Blender, reviewed, and exported with blender_export_stl."
        ),
        "verification": (
            "Print dimensions and geometry stats, frame object, then take viewport screenshot."
        ),
        "slicing": (
            "Use OrcaSlicer/Elegoo-compatible profiles; save .3mf when settings differ "
            "from defaults."
        ),
        "gcode_validation": (
            "Use gcode_validate_file before upload, or set validate_gcode=true on "
            "orcaslicer_slice_model to validate generated G-code automatically."
        ),
    }
    return json.dumps(payload, indent=2)


def _format_validation_error(error: ValidationError) -> str:
    details = []
    for item in error.errors():
        location = ".".join(str(part) for part in item.get("loc", ())) or "request"
        message = item.get("msg", "Invalid value")
        details.append(f"{location}: {message}")
    return "Invalid MCP tool request. " + "; ".join(details)


def _dump_model(model: BaseModel) -> JsonObject:
    return cast(JsonObject, model.model_dump(mode="json"))


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
