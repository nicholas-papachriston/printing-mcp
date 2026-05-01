from __future__ import annotations

import time
from pathlib import Path
from typing import cast

from ..types import JsonObject, ToolResult
from .backend_client import GenerationBackendClient
from .cost import estimate_image_cost_usd
from .files import append_generation_call, default_record_path, resolve_generation_path
from .jobs import GenerationJob, GenerationJobStore
from .schemas import GenerationCreateImageRequest


class GenerationImageRunner:
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

    def create_image(self, request: GenerationCreateImageRequest) -> ToolResult:
        start = time.monotonic()
        if request.provider != "modal":
            return self._failure("provider_unavailable", "Only provider='modal' is supported.")

        estimated_cost = estimate_image_cost_usd()
        if estimated_cost > request.max_cost_usd:
            return self._failure("cost_limit_exceeded", "Estimated image cost exceeds limit.")

        output_path = resolve_generation_path(
            self.workspace_root,
            request.model_name,
            request.version,
            request.output_path,
            label="output_path",
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

        job = self.job_store.create(tool_name="generation_create_image", provider=request.provider)
        try:
            backend_result = self.backend_client.create_image(
                prompt=request.prompt,
                negative_prompt=request.negative_prompt,
                seed=request.seed,
                width=request.width,
                height=request.height,
                output_path=output_path,
                max_cost_usd=request.max_cost_usd,
            )
            elapsed_seconds = round(time.monotonic() - start, 3)
            append_generation_call(
                record_path,
                model_name=request.model_name,
                version=request.version,
                tool_name="generation_create_image",
                status="completed",
                inputs={
                    "prompt": request.prompt,
                    "negative_prompt": request.negative_prompt,
                    "seed": request.seed,
                    "width": request.width,
                    "height": request.height,
                },
                outputs=backend_result,
            )
        except (OSError, RuntimeError, ValueError) as error:
            error_code = _image_error_code(error)
            self.job_store.set_failed(
                job,
                error={
                    "error_code": error_code,
                    "message": str(error),
                    "retryable": error_code in {"output_write_failed", "provider_job_failed"},
                },
            )
            return self._job_failure(job)

        result = {
            "status": "completed",
            "job_id": job.job_id,
            "provider": request.provider,
            "tool_name": "generation_create_image",
            "model": "nano_banana_2",
            "elapsed_seconds": elapsed_seconds,
            "estimated_cost_usd": estimated_cost,
            "output_path": str(output_path),
            "record_path": str(record_path),
            "next_tool": "generation_remove_background",
        }
        self.job_store.set_completed(job, result=result, record_path=record_path)
        return ToolResult(ok=True, backend="generation", data=cast(JsonObject, result))

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
            "message": "Generation image job failed.",
            "retryable": True,
        }
        return ToolResult(
            ok=False,
            backend="generation",
            error_type="backend_error",
            message=str(error["message"]),
            data=cast(JsonObject, {"status": "failed", "job_id": job.job_id, **error}),
        )


def _image_error_code(error: Exception) -> str:
    message = str(error).lower()
    if "token" in message or "auth" in message or "credential" in message or "api key" in message:
        return "provider_auth_missing"
    if isinstance(error, OSError):
        return "output_write_failed"
    return "provider_job_failed"
