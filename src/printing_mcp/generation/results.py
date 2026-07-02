from __future__ import annotations

from typing import cast

from ..types import JsonObject, ToolResult
from .jobs import GenerationJob


def generation_failure(
    error_code: str,
    message: str,
    *,
    retryable_codes: set[str],
    status: str = "failed",
) -> ToolResult:
    return ToolResult(
        ok=False,
        backend="generation",
        error_type="backend_error",
        message=message,
        data={
            "status": status,
            "error_code": error_code,
            "message": message,
            "retryable": error_code in retryable_codes,
        },
    )


def generation_job_failure(job: GenerationJob, *, default_message: str) -> ToolResult:
    error = job.error or {
        "error_code": "provider_job_failed",
        "message": default_message,
        "retryable": True,
    }
    return ToolResult(
        ok=False,
        backend="generation",
        error_type="backend_error",
        message=str(error["message"]),
        data=cast(JsonObject, {"status": "failed", "job_id": job.job_id, **error}),
    )
