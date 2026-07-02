from __future__ import annotations

import time
from typing import cast

from ..types import JsonObject, ToolResult
from .cost import estimate_image_cost_usd
from .dependencies import GenerationDependencies
from .files import append_generation_call, resolve_generation_path, resolve_generation_record_path
from .jobs import GenerationJob
from .results import generation_failure, generation_job_failure
from .schemas import GenerationCreateImageRequest

IMAGE_RETRYABLE_ERRORS = {"provider_unavailable", "output_write_failed"}


class GenerationImageRunner:
    def __init__(self, dependencies: GenerationDependencies) -> None:
        self.dependencies = dependencies

    def create_image(self, request: GenerationCreateImageRequest) -> ToolResult:
        start = time.monotonic()
        if request.provider != "modal":
            return _image_failure("provider_unavailable", "Only provider='modal' is supported.")

        estimated_cost = estimate_image_cost_usd()
        if estimated_cost > request.max_cost_usd:
            return _image_failure("cost_limit_exceeded", "Estimated image cost exceeds limit.")

        output_path = resolve_generation_path(
            self.dependencies.workspace_root,
            request.model_name,
            request.version,
            request.output_path,
            label="output_path",
        )
        record_path = resolve_generation_record_path(
            self.dependencies.workspace_root,
            request.model_name,
            request.version,
            request.record_path,
        )

        job = self.dependencies.job_store.create(
            tool_name="generation_create_image", provider=request.provider
        )
        try:
            backend_result = self.dependencies.backend_client.create_image(
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
                provider=request.provider,
                inputs={
                    "prompt": request.prompt,
                    "negative_prompt": request.negative_prompt,
                    "seed": request.seed,
                    "width": request.width,
                    "height": request.height,
                },
                outputs=backend_result.to_record(),
            )
        except (OSError, RuntimeError, ValueError) as error:
            error_code = _image_error_code(error)
            self.dependencies.job_store.set_failed(
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
        self.dependencies.job_store.set_completed(job, result=result, record_path=record_path)
        return ToolResult(ok=True, backend="generation", data=cast(JsonObject, result))

    def _job_failure(self, job: GenerationJob) -> ToolResult:
        return generation_job_failure(job, default_message="Generation image job failed.")


def _image_failure(error_code: str, message: str) -> ToolResult:
    return generation_failure(
        error_code,
        message,
        retryable_codes=IMAGE_RETRYABLE_ERRORS,
        status="failed",
    )


def _image_error_code(error: Exception) -> str:
    message = str(error).lower()
    if "token" in message or "auth" in message or "credential" in message or "api key" in message:
        return "provider_auth_missing"
    if isinstance(error, OSError):
        return "output_write_failed"
    return "provider_job_failed"
