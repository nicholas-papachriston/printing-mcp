from __future__ import annotations

import time
from pathlib import Path
from typing import cast

from ..types import JsonObject, ToolResult
from .backend_client import GenerationBackendClient
from .cost import estimate_fast_mesh_cost_usd, estimate_warmup_cost_usd
from .files import append_generation_call, default_record_path, resolve_generation_path
from .jobs import GenerationJob, GenerationJobStore
from .schemas import (
    GenerationCancelJobRequest,
    GenerationCreateMeshRequest,
    GenerationGetJobRequest,
    GenerationWarmBackendRequest,
)

SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class GenerationMeshRunner:
    def __init__(
        self,
        *,
        workspace_root: Path,
        backend_client: GenerationBackendClient,
        job_store: GenerationJobStore,
    ) -> None:
        self.workspace_root = workspace_root
        self.backend_client = backend_client
        self.job_store = job_store

    def warm_backend(self, request: GenerationWarmBackendRequest) -> ToolResult:
        estimated_cost = estimate_warmup_cost_usd()
        if request.provider != "modal":
            return self._failure("provider_unavailable", "Only provider='modal' is supported.")
        if not request.confirm_billable_warmup:
            return self._failure(
                "warmup_not_confirmed",
                "confirm_billable_warmup must be true for backend warmup.",
            )
        if estimated_cost > request.max_cost_usd:
            return self._failure("cost_limit_exceeded", "Estimated warmup cost exceeds limit.")
        job = self.job_store.create(tool_name="generation_warm_backend", provider=request.provider)
        self.job_store.set_completed(
            job,
            result={
                "status": "completed",
                "job_id": job.job_id,
                "provider": request.provider,
                "tool_name": "generation_warm_backend",
                "models": request.models,
                "estimated_cost_usd": estimated_cost,
                "warmup_mode": "acknowledged",
                "next_tool": "generation_create_mesh",
            },
            record_path=None,
        )
        return ToolResult(ok=True, backend="generation", data=cast(JsonObject, job.result))

    def create_mesh(self, request: GenerationCreateMeshRequest) -> ToolResult:
        start = time.monotonic()
        validation_error = self._validate_mesh_request(request)
        if validation_error is not None:
            return validation_error

        estimated_cost = estimate_fast_mesh_cost_usd()
        if estimated_cost > request.max_cost_usd:
            return self._failure("cost_limit_exceeded", "Estimated mesh cost exceeds limit.")

        source_image_path = resolve_generation_path(
            self.workspace_root,
            request.model_name,
            request.version,
            request.source_image_path,
            label="source_image_path",
        )
        output_raw_stl_path = resolve_generation_path(
            self.workspace_root,
            request.model_name,
            request.version,
            request.output_raw_stl_path,
            label="output_raw_stl_path",
        )
        output_raw_glb_path = (
            resolve_generation_path(
                self.workspace_root,
                request.model_name,
                request.version,
                request.output_raw_glb_path,
                label="output_raw_glb_path",
            )
            if request.output_raw_glb_path is not None
            else None
        )
        record_path = (
            resolve_generation_path(
                self.workspace_root,
                request.model_name,
                request.version,
                request.record_path,
                label="record_path",
            )
            if request.record_path is not None
            else default_record_path(self.workspace_root, request.model_name, request.version)
        )

        if not source_image_path.is_file():
            return self._failure(
                "input_not_found", f"Source image does not exist: {source_image_path}"
            )
        if source_image_path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
            return self._failure(
                "unsupported_image_format", "Source image must be PNG, JPEG, or WebP."
            )

        job = self.job_store.create(tool_name="generation_create_mesh", provider=request.provider)
        try:
            backend_result = self.backend_client.create_fast_mesh(
                prompt=f"mesh from {source_image_path.name}",
                source_image_path=source_image_path,
                target_size_mm=request.target_size_mm,
                output_raw_stl_path=output_raw_stl_path,
                output_raw_glb_path=output_raw_glb_path,
                max_cost_usd=request.max_cost_usd,
            )
            elapsed_seconds = round(time.monotonic() - start, 3)
            append_generation_call(
                record_path,
                model_name=request.model_name,
                version=request.version,
                tool_name="generation_create_mesh",
                status="completed",
                inputs={
                    "source_image_path": str(source_image_path),
                    "mesh_model": request.mesh_model,
                    "quality": request.quality,
                    "seed": request.seed,
                },
                outputs=backend_result,
            )
        except (OSError, RuntimeError, ValueError) as error:
            error_code = _mesh_error_code(error)
            self.job_store.set_failed(
                job,
                error={
                    "error_code": error_code,
                    "message": str(error),
                    "retryable": error_code
                    in {"provider_job_failed", "modal_auth_missing", "model_cache_missing"},
                },
            )
            return self._job_failure(job)

        result = {
            "status": "completed",
            "job_id": job.job_id,
            "provider": request.provider,
            "tool_name": "generation_create_mesh",
            "mesh_model": request.mesh_model,
            "quality": request.quality,
            "elapsed_seconds": elapsed_seconds,
            "estimated_cost_usd": estimated_cost,
            "source_image_path": str(source_image_path),
            "output_raw_stl_path": str(output_raw_stl_path),
            "output_raw_glb_path": str(output_raw_glb_path) if output_raw_glb_path else None,
            "record_path": str(record_path),
            "mesh": backend_result["mesh"],
            "next_tool": "blender_execute_code",
        }
        self.job_store.set_completed(
            job,
            result=result,
            record_path=record_path,
            output_raw_stl_path=output_raw_stl_path,
            output_raw_glb_path=output_raw_glb_path,
        )
        return ToolResult(ok=True, backend="generation", data=cast(JsonObject, result))

    def get_job(self, request: GenerationGetJobRequest) -> ToolResult:
        job = self.job_store.get(request.job_id)
        if job is None:
            return self._failure("job_not_found", f"No generation job found: {request.job_id}")
        deadline = time.monotonic() + min(request.wait_seconds, request.timeout_seconds)
        while (
            job.status not in {"completed", "cancelled", "failed"} and time.monotonic() < deadline
        ):
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
        if job.status == "completed":
            return ToolResult(ok=True, backend="generation", data=cast(JsonObject, job.result))
        if job.status == "cancelled":
            return ToolResult(
                ok=False,
                backend="generation",
                error_type="backend_error",
                message=str(job.error["message"]) if job.error else "Job was cancelled.",
                data={"status": "cancelled", "job_id": job.job_id, "provider": job.provider},
            )
        if job.status == "failed":
            return self._job_failure(job)
        return ToolResult(
            ok=True,
            backend="generation",
            data={
                "status": job.status,
                "job_id": job.job_id,
                "provider": job.provider,
                "tool_name": job.tool_name,
                "next_tool": "generation_get_job",
            },
        )

    def cancel_job(self, request: GenerationCancelJobRequest) -> ToolResult:
        job = self.job_store.get(request.job_id)
        if job is None:
            return self._failure("job_not_found", f"No generation job found: {request.job_id}")
        if not self.job_store.cancel(job, reason=request.reason):
            return self._failure("job_already_terminal", f"Job is already {job.status}.")
        return ToolResult(
            ok=True,
            backend="generation",
            data={
                "status": "cancelled",
                "job_id": job.job_id,
                "provider": job.provider,
                "tool_name": "generation_cancel_job",
                "message": request.reason,
                "record_path": str(job.record_path) if job.record_path else None,
            },
        )

    def _validate_mesh_request(self, request: GenerationCreateMeshRequest) -> ToolResult | None:
        if request.provider != "modal":
            return self._failure("provider_unavailable", "Only provider='modal' is supported.")
        if request.mesh_model != "triposr":
            return self._failure(
                "unsupported_mesh_model", "Only mesh_model='triposr' is implemented."
            )
        if request.quality != "fast":
            return self._failure("model_quality_mismatch", "Only quality='fast' is implemented.")
        if any(value <= 0 for value in request.target_size_mm):
            return self._failure(
                "invalid_target_size", "target_size_mm values must be greater than zero."
            )
        return None

    def _failure(self, error_code: str, message: str) -> ToolResult:
        return ToolResult(
            ok=False,
            backend="generation",
            error_type="backend_error",
            message=message,
            data={
                "status": "failed",
                "error_code": error_code,
                "message": message,
                "retryable": error_code in {"provider_unavailable", "output_write_failed"},
            },
        )

    def _job_failure(self, job: GenerationJob) -> ToolResult:
        error = job.error or {
            "error_code": "provider_job_failed",
            "message": "Generation job failed.",
            "retryable": True,
        }
        return ToolResult(
            ok=False,
            backend="generation",
            error_type="backend_error",
            message=str(error["message"]),
            data=cast(JsonObject, {"status": "failed", "job_id": job.job_id, **error}),
        )


def _mesh_error_code(error: Exception) -> str:
    message = str(error).lower()
    if "token" in message or "auth" in message or "credential" in message:
        return "modal_auth_missing"
    if "cache" in message and ("missing" in message or "not found" in message):
        return "model_cache_missing"
    if "artifact_too_large" in message:
        return "artifact_too_large"
    if "mesh_empty" in message or "no vertices" in message or "no faces" in message:
        return "mesh_empty"
    if isinstance(error, OSError):
        return "output_write_failed"
    return "provider_job_failed"
