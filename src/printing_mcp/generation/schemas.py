from __future__ import annotations

from pydantic import BaseModel, Field


class GenerationCreateImageRequest(BaseModel):
    model_name: str = Field(
        pattern=r"^[a-z][a-z0-9_]*$", description="Unversioned model folder name."
    )
    version: int = Field(ge=1, description="Model version number.")
    prompt: str = Field(min_length=1, description="Image prompt for the source object.")
    negative_prompt: str | None = Field(default=None, description="Optional prompt text to avoid.")
    seed: int | None = Field(default=None, description="Optional seed recorded in provenance.")
    width: int = Field(default=1024, ge=64, le=2048, description="Requested image width.")
    height: int = Field(default=1024, ge=64, le=2048, description="Requested image height.")
    output_path: str = Field(
        description="Output image path, usually under <model>/v<N>/generation/."
    )
    record_path: str | None = Field(
        default=None,
        description=(
            "Optional generation.json path; defaults to the model version generation folder."
        ),
    )
    provider: str = Field(
        default="modal", description="Generation provider; only 'modal' is supported."
    )
    max_cost_usd: float = Field(default=0.03, gt=0, description="Maximum allowed image cost.")
    wait_for_completion: bool = Field(
        default=False,
        description=(
            "Accepted for API compatibility; current image generation returns completed results."
        ),
    )
    timeout_seconds: float = Field(default=30.0, gt=0, description="Requested provider timeout.")


class GenerationRemoveBackgroundRequest(BaseModel):
    model_name: str = Field(
        pattern=r"^[a-z][a-z0-9_]*$", description="Unversioned model folder name."
    )
    version: int = Field(ge=1, description="Model version number.")
    source_path: str = Field(description="Input image path under the model version folder.")
    output_path: str = Field(description="Output cutout path under the model version folder.")
    record_path: str | None = Field(
        default=None,
        description=(
            "Optional generation.json path; defaults to the model version generation folder."
        ),
    )
    force: bool = Field(default=False, description="Overwrite output_path when it already exists.")
    timeout_seconds: float = Field(default=60.0, gt=0, description="Local operation timeout.")


class GenerationCreateMeshRequest(BaseModel):
    model_name: str = Field(
        pattern=r"^[a-z][a-z0-9_]*$", description="Unversioned model folder name."
    )
    version: int = Field(ge=1, description="Model version number.")
    source_image_path: str = Field(description="Source PNG/JPEG/WebP image for mesh generation.")
    quality: str = Field(
        default="fast", description="Mesh quality tier; only 'fast' is implemented."
    )
    mesh_model: str = Field(
        default="triposr", description="Mesh model; only 'triposr' is implemented."
    )
    output_raw_stl_path: str = Field(description="Raw generated STL output path.")
    output_raw_glb_path: str | None = Field(
        default=None, description="Optional raw generated GLB path."
    )
    record_path: str | None = Field(
        default=None,
        description=(
            "Optional generation.json path; defaults to the model version generation folder."
        ),
    )
    target_size_mm: tuple[float, float, float] = Field(
        description="Target raw mesh bounding box in millimeters."
    )
    provider: str = Field(
        default="modal", description="Generation provider; only 'modal' is supported."
    )
    seed: int | None = Field(default=None, description="Optional seed recorded in provenance.")
    max_cost_usd: float = Field(
        default=0.08, gt=0, description="Maximum allowed mesh generation cost."
    )
    wait_for_completion: bool = Field(
        default=False,
        description=(
            "Accepted for API compatibility; current mesh generation returns completed results."
        ),
    )
    timeout_seconds: float = Field(default=30.0, gt=0, description="Requested provider timeout.")


class GenerationGetJobRequest(BaseModel):
    job_id: str = Field(
        min_length=1, description="Generation job ID returned by a generation tool."
    )
    wait_seconds: float = Field(
        default=0.0, ge=0, description="Optional time to wait for completion."
    )
    timeout_seconds: float = Field(default=30.0, gt=0, description="Maximum polling wait.")


class GenerationCancelJobRequest(BaseModel):
    job_id: str = Field(min_length=1, description="Generation job ID to cancel.")
    reason: str = Field(default="User requested cancellation.", description="Cancellation reason.")


class GenerationWarmBackendRequest(BaseModel):
    provider: str = Field(
        default="modal", description="Generation provider; only 'modal' is supported."
    )
    models: list[str] = Field(
        default_factory=lambda: ["flux_1_schnell", "triposr"],
        description="Backend models the caller intends to warm or prepare.",
    )
    max_cost_usd: float = Field(default=0.50, gt=0, description="Maximum allowed warmup cost.")
    confirm_billable_warmup: bool = Field(
        default=False,
        description="Must be true to acknowledge that backend warmup can be billable.",
    )
    wait_for_completion: bool = Field(
        default=False,
        description="Accepted for API compatibility; current warmup acknowledgement is immediate.",
    )
    timeout_seconds: float = Field(default=30.0, gt=0, description="Requested provider timeout.")
