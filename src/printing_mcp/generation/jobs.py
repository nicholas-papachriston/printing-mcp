from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


@dataclass
class GenerationJob:
    job_id: str
    tool_name: str
    status: str
    provider: str
    created_at: str
    record_path: Path | None = None
    output_raw_stl_path: Path | None = None
    output_raw_glb_path: Path | None = None
    result: dict[str, object] = field(default_factory=dict)
    error: dict[str, object] | None = None


class GenerationJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, GenerationJob] = {}

    def create(self, *, tool_name: str, provider: str) -> GenerationJob:
        prefix = "gen_"
        job = GenerationJob(
            job_id=f"{prefix}{uuid4().hex}",
            tool_name=tool_name,
            status="queued",
            provider=provider,
            created_at=datetime.now(UTC).isoformat(),
        )
        self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> GenerationJob | None:
        return self._jobs.get(job_id)

    def set_completed(
        self,
        job: GenerationJob,
        *,
        result: dict[str, object],
        record_path: Path | None,
        output_raw_stl_path: Path | None = None,
        output_raw_glb_path: Path | None = None,
    ) -> None:
        job.status = "completed"
        job.result = result
        job.record_path = record_path
        job.output_raw_stl_path = output_raw_stl_path
        job.output_raw_glb_path = output_raw_glb_path

    def set_failed(self, job: GenerationJob, *, error: dict[str, object]) -> None:
        job.status = "failed"
        job.error = error

    def cancel(self, job: GenerationJob, *, reason: str) -> bool:
        if job.status in {"completed", "failed", "cancelled"}:
            return False
        job.status = "cancelled"
        job.error = {"error_code": "cancelled", "message": reason, "retryable": False}
        return True
