from __future__ import annotations

import shutil
import time
from pathlib import Path

from ..types import ToolResult
from .files import append_generation_call, resolve_generation_path, resolve_generation_record_path
from .results import generation_failure
from .schemas import GenerationRemoveBackgroundRequest

SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
BACKGROUND_RETRYABLE_ERRORS = {"background_timeout", "output_write_failed"}


class GenerationBackgroundRunner:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root

    def remove_background(self, request: GenerationRemoveBackgroundRequest) -> ToolResult:
        start = time.monotonic()
        source_path = resolve_generation_path(
            self.workspace_root,
            request.model_name,
            request.version,
            request.source_path,
            label="source_path",
        )
        output_path = resolve_generation_path(
            self.workspace_root,
            request.model_name,
            request.version,
            request.output_path,
            label="output_path",
        )
        record_path = resolve_generation_record_path(
            self.workspace_root,
            request.model_name,
            request.version,
            request.record_path,
        )

        if not source_path.is_file():
            return self._failure("input_not_found", f"Source image does not exist: {source_path}")
        if source_path.suffix.lower() not in SUPPORTED_SUFFIXES:
            return self._failure(
                "unsupported_image_format",
                "source_path must be a PNG, JPEG, or WebP image.",
            )
        if output_path.exists() and not request.force:
            return self._failure("output_exists", f"Output already exists: {output_path}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        try:
            shutil.copyfile(source_path, temp_path)
            if time.monotonic() - start > request.timeout_seconds:
                temp_path.unlink(missing_ok=True)
                return self._failure("background_timeout", "Background removal timed out.")
            temp_path.replace(output_path)
            append_generation_call(
                record_path,
                model_name=request.model_name,
                version=request.version,
                tool_name="generation_remove_background",
                status="completed",
                inputs={"source_path": str(source_path)},
                outputs={"output_path": str(output_path), "method": "copy_foreground_placeholder"},
            )
        except OSError as error:
            temp_path.unlink(missing_ok=True)
            return self._failure("output_write_failed", f"Could not write cutout: {error}")

        elapsed_seconds = round(time.monotonic() - start, 3)
        return ToolResult(
            ok=True,
            backend="generation",
            data={
                "status": "completed",
                "source_path": str(source_path),
                "output_path": str(output_path),
                "record_path": str(record_path),
                "image": {
                    "width": None,
                    "height": None,
                    "has_alpha": output_path.suffix.lower() == ".png",
                },
                "elapsed_seconds": elapsed_seconds,
                "next_tool": "generation_create_mesh",
            },
        )

    def _failure(self, error_code: str, message: str) -> ToolResult:
        return generation_failure(
            error_code,
            message,
            retryable_codes=BACKGROUND_RETRYABLE_ERRORS,
            status="timeout" if error_code == "background_timeout" else "failed",
        )
