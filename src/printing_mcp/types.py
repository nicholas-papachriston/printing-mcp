from __future__ import annotations

from pathlib import Path
from typing import Literal, TypeAlias

from pydantic import BaseModel, Field, JsonValue, field_validator

JsonObject: TypeAlias = dict[str, JsonValue]

BackendName = Literal["authoring", "blender", "elegoo", "generation", "orcaslicer", "validation"]
ErrorType = Literal[
    "missing_binary",
    "connection_error",
    "timeout",
    "invalid_request",
    "validation_error",
    "backend_error",
    "discovery_error",
    "upload_error",
    "spawn_error",
    "orcaslicer_cli_error",
]

PrinterType = Literal["cc2", "cc"]
PrintStorage = Literal["local", "udisk", "sdcard"]


class DiscoveredFile(BaseModel):
    """File produced or updated by a backend command."""

    path: str
    kind: str
    size_bytes: int


class ToolResult(BaseModel):
    """Shared MCP tool result shape."""

    ok: bool
    backend: BackendName | None = None
    error_type: ErrorType | None = None
    message: str | None = None
    suggestions: list[str] = Field(default_factory=list)
    data: JsonObject | None = None


class CommandResult(ToolResult):
    """Structured result for an OrcaSlicer CLI invocation."""

    backend: BackendName | None = "orcaslicer"
    returncode: int | None
    command: list[str]
    cwd: str | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    timeout_seconds: float | None = None
    generated_files: list[DiscoveredFile] = Field(default_factory=list)
    result_json: JsonObject | None = None


class ElegooSlotMapEntry(BaseModel):
    """CANVAS/MMS filament slot mapping for Elegoo start-print requests."""

    t: int = Field(ge=0)
    canvas_id: int = Field(ge=0)
    tray_id: int = Field(ge=0)


class BlenderCodeRequest(BaseModel):
    """Request for executing Python in Blender."""

    code: str = Field(min_length=1)
    timeout_seconds: float | None = Field(default=None, gt=0)


class ObjectInfoRequest(BaseModel):
    """Request for a single Blender object's information."""

    object_name: str = Field(min_length=1)


class ViewportScreenshotRequest(BaseModel):
    """Request for a Blender viewport screenshot."""

    max_size: int = Field(default=800, ge=100, le=4096)


class BlenderValidateObjectRequest(BaseModel):
    """Request for mesh/printability validation of a Blender object."""

    object_name: str = Field(min_length=1)
    evaluated: bool = True


class BlenderExportStlRequest(BlenderValidateObjectRequest):
    """Request for validated STL export from Blender."""

    target_path: str
    global_scale: float = Field(default=1000.0, gt=0)
    validate_mesh: bool = True
    allow_non_manifold: bool = False


class BlenderPrintabilityReviewRequest(BlenderValidateObjectRequest):
    """Request for heuristic printability review of a Blender mesh."""

    bed_size_mm: tuple[float, float, float] = Field(default=(256.0, 256.0, 256.0))
    overhang_degrees: float = Field(default=45.0, ge=0, le=90)
    min_feature_mm: float = Field(default=0.4, gt=0)
    require_single_component: bool = False


class BlenderVisualReviewRequest(BlenderValidateObjectRequest):
    """Request for multi-angle Blender viewport screenshots."""

    output_dir: str
    max_size: int = Field(default=1200, ge=100, le=4096)
    include_top: bool = True
    include_front: bool = True
    include_right: bool = True
    include_isometric: bool = True


class BlenderBootstrapModelRequest(BaseModel):
    """Request for creating a repo-standard model/version folder and empty scene."""

    model_name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: int = Field(default=1, ge=1)
    output_root: str | None = None

    @field_validator("model_name")
    @classmethod
    def reject_versioned_model_name(cls, value: str) -> str:
        if len(value) > 2 and value.rsplit("_v", maxsplit=1)[-1].isdigit():
            raise ValueError(
                "model_name must be the unversioned top-level folder name, "
                "for example 'gecko_hide' not 'gecko_hide_v1'."
            )
        return value


class ElegooPrinterInfo(BaseModel):
    """Discovered or configured LAN-only Elegoo printer."""

    printer_id: str
    host: str
    printer_type: PrinterType
    name: str | None = None
    model: str | None = None
    serial_number: str | None = None
    mainboard_id: str | None = None
    firmware_version: str | None = None
    auth_mode: str | None = None
    requires_access_code: bool = False
    network_mode: Literal["lan"] = "lan"


class ElegooPrinterRequest(BaseModel):
    """Common target fields for LAN-only Elegoo machine tools."""

    host: str | None = None
    printer_type: PrinterType | None = None
    access_code: str | None = None
    serial_number: str | None = None
    mainboard_id: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)


class ElegooDiscoveryRequest(BaseModel):
    """Request for UDP LAN discovery."""

    timeout_seconds: float | None = Field(default=None, gt=0)
    include_cc2: bool = True
    include_cc: bool = True


class ElegooUploadRequest(ElegooPrinterRequest):
    """Upload a local G-code/3MF file to a LAN printer."""

    file_path: str
    file_name: str | None = None
    remote_path: str | None = None
    chunk_bytes: int | None = Field(default=None, gt=0, le=1024 * 1024)


class ElegooStartPrintRequest(ElegooPrinterRequest):
    """Start a file that already exists on the LAN printer."""

    file_name: str = Field(min_length=1)
    storage: PrintStorage = "local"
    bed_type: int = Field(default=0, ge=0)
    time_lapse: bool = False
    heated_bed_leveling: bool = True
    bed_level_force: bool = False
    slot_map: list[ElegooSlotMapEntry] = Field(default_factory=list)


class ElegooUploadAndStartRequest(ElegooUploadRequest):
    """Upload a local file to a LAN printer and start it."""

    storage: PrintStorage = "local"
    bed_type: int = Field(default=0, ge=0)
    time_lapse: bool = False
    heated_bed_leveling: bool = True
    bed_level_force: bool = False
    slot_map: list[ElegooSlotMapEntry] = Field(default_factory=list)


class CommonRequest(BaseModel):
    """Shared options accepted by OrcaSlicer CLI wrapper tools."""

    model_paths: list[str] = Field(default_factory=list)
    output_dir: str | None = None
    datadir: str | None = None
    settings_paths: list[str] = Field(default_factory=list)
    filament_paths: list[str] = Field(default_factory=list)
    timeout_seconds: float | None = Field(default=None, gt=0)
    extra_args: list[str] = Field(default_factory=list)


class GCodeValidationOptions(BaseModel):
    """Common post-slice G-code validation thresholds."""

    bed_size_mm: tuple[float, float, float] = Field(
        default=(256.0, 256.0, 256.0),
        description="Printer build volume in millimeters, used for XY and default Z bounds.",
    )
    check_bed_bounds: bool = Field(
        default=True,
        description="When true, flag XY moves outside the rectangular bed from (0,0) to X/Y size.",
    )
    max_oob_hits_reported: int = Field(
        default=20,
        ge=1,
        le=200,
        description="Maximum number of out-of-bounds moves included in the response sample.",
    )
    max_nozzle_temp_c: float = Field(
        default=350.0,
        gt=0,
        description="Maximum allowed nozzle temperature command in Celsius.",
    )
    max_bed_temp_c: float = Field(
        default=110.0,
        gt=0,
        description="Maximum allowed bed temperature command in Celsius.",
    )
    min_wait_nozzle_temp_c: float | None = Field(
        default=None,
        ge=0,
        description="Optional lower bound for the first non-zero M109 wait temperature.",
    )
    max_wait_nozzle_temp_c: float | None = Field(
        default=None,
        gt=0,
        description="Optional upper bound for the first non-zero M109 wait temperature.",
    )
    min_wait_bed_temp_c: float | None = Field(
        default=None,
        ge=0,
        description="Optional lower bound for the first non-zero M190 bed wait temperature.",
    )
    max_wait_bed_temp_c: float | None = Field(
        default=None,
        gt=0,
        description="Optional upper bound for the first non-zero M190 bed wait temperature.",
    )
    max_print_time_seconds: float | None = Field(
        default=None,
        gt=0,
        description="Optional estimated print time budget in seconds.",
    )
    max_filament_weight_g: float | None = Field(
        default=None,
        gt=0,
        description="Optional estimated filament budget in grams.",
    )
    max_z_mm: float | None = Field(
        default=None,
        gt=0,
        description="Optional Z-height limit; defaults to bed_size_mm[2] when omitted.",
    )
    require_extrusion: bool = Field(
        default=True,
        description="When true, fail files that contain no extrusion moves.",
    )
    filament_type: str | None = Field(
        default=None,
        description="Optional filament type hint for material estimate density lookup.",
    )
    filament_density: float | None = Field(
        default=None,
        gt=0,
        description="Optional filament density override in g/cm^3 for weight estimates.",
    )


class SliceRequest(CommonRequest):
    """Request for slicing one or more models."""

    model_paths: list[str] = Field(min_length=1)
    output_dir: str
    plate: int = Field(default=0, ge=0, description="0 slices all plates; i slices plate i.")
    arrange: int | None = None
    orient: int | None = None
    scale: float | None = Field(default=None, gt=0)
    validate_gcode: bool = Field(
        default=False,
        description="Run gcode_validate_file on generated .gcode/.bgcode outputs before success.",
    )
    gcode_validation: GCodeValidationOptions = Field(
        default_factory=GCodeValidationOptions,
        description="Thresholds used when validate_gcode is true.",
    )


class Export3mfRequest(CommonRequest):
    """Request for exporting a project as 3MF."""

    model_paths: list[str] = Field(min_length=1)
    target_path: str
    min_save: bool = False


class ExportStlRequest(CommonRequest):
    """Request for exporting STL from one or more models."""

    model_paths: list[str] = Field(min_length=1)
    mode: Literal["single", "multiple"] = "single"
    target_dir: str | None = None


class GCodeValidationRequest(GCodeValidationOptions):
    """Request for validating a sliced G-code or BGCode file."""

    file_path: str = Field(description="Path to a sliced .gcode or .bgcode file.")


class MeshArtifactValidationRequest(BaseModel):
    """Request for validating an STL/mesh artifact outside Blender."""

    file_path: str = Field(
        description="Path to an STL, OBJ, GLB, 3MF, or other trimesh-readable mesh."
    )
    bed_size_mm: tuple[float, float, float] = Field(
        default=(256.0, 256.0, 256.0),
        description="Printer build volume in millimeters for dimension checks.",
    )
    require_watertight: bool = Field(
        default=True,
        description="When true, fail meshes that are not watertight.",
    )
    require_positive_volume: bool = Field(
        default=True,
        description="When true, fail meshes without non-zero positive volume.",
    )
    require_single_component: bool = Field(
        default=False,
        description="When true, fail meshes with more than one connected component.",
    )
    require_z0: bool = Field(
        default=False,
        description="When true, require the mesh minimum Z to be near 0 mm.",
    )
    z_tolerance_mm: float = Field(
        default=0.05,
        ge=0,
        description="Allowed absolute distance from Z=0 when require_z0 is true.",
    )
    max_faces: int | None = Field(
        default=None,
        gt=0,
        description="Optional maximum triangle/face count for generated artifacts.",
    )


class InspectModelRequest(CommonRequest):
    """Request for printing OrcaSlicer's model information output."""

    model_paths: list[str] = Field(min_length=1)


class ExportSettingsRequest(CommonRequest):
    """Request for exporting resolved project settings."""

    model_paths: list[str] = Field(default_factory=list)
    target_path: str


class BackendStatus(BaseModel):
    """Availability for one backend."""

    available: bool
    message: str | None = None
    suggestions: list[str] = Field(default_factory=list)


class StatusResult(BaseModel):
    """Current printing MCP configuration and backend availability."""

    blender: BackendStatus
    elegoo: BackendStatus
    orcaslicer: BackendStatus
    binary_path: str | None = None
    datadir: str | None = None
    blender_host: str | None = None
    blender_port: int | None = None
    elegoo_host: str | None = None
    timeout_seconds: float
    help_excerpt: list[str] = Field(default_factory=list)
    message: str | None = None


PathLike = str | Path
