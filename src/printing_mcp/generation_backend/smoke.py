from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any, Protocol

from .artifacts import (
    SmokeArtifacts,
    ensure_output_dir,
    write_manifest,
    write_placeholder_glb,
    write_placeholder_png,
    write_placeholder_stl,
)
from .model_versions import fast_model_manifest


class RemoteMeshFunction(Protocol):
    def remote(self, request: dict[str, Any]) -> dict[str, Any]: ...


def run_fast_smoke(
    *,
    prompt: str,
    target_size_mm: tuple[float, float, float],
    output_dir: Path,
    max_cost_usd: float,
    dry_run: bool = True,
    modal_create_mesh: RemoteMeshFunction | None = None,
) -> SmokeArtifacts:
    if max_cost_usd <= 0:
        raise ValueError("max_cost_usd must be greater than zero.")
    if any(value <= 0 for value in target_size_mm):
        raise ValueError("target_size_mm values must be greater than zero.")

    start = time.monotonic()
    ensure_output_dir(output_dir)

    artifacts = SmokeArtifacts(
        source_image=output_dir / "smoke_source.png",
        cutout_image=output_dir / "smoke_cutout.png",
        raw_stl=output_dir / "smoke_raw.stl",
        raw_glb=output_dir / "smoke_raw.glb",
        manifest=output_dir / "smoke_manifest.json",
    )

    if dry_run:
        write_placeholder_png(artifacts.source_image)
        write_placeholder_png(artifacts.cutout_image)
        write_placeholder_stl(artifacts.raw_stl, target_size_mm)
        write_placeholder_glb(artifacts.raw_glb)
        real_manifest: dict[str, Any] = {}
    else:
        if modal_create_mesh is None:
            raise ValueError("modal_create_mesh is required when dry_run is false.")
        write_placeholder_png(artifacts.source_image)
        artifacts.cutout_image.write_bytes(artifacts.source_image.read_bytes())
        result = modal_create_mesh.remote(
            {
                "image_b64": base64.b64encode(artifacts.cutout_image.read_bytes()).decode("ascii"),
                "target_size_mm": list(target_size_mm),
                "max_cost_usd": max_cost_usd,
                "seed": None,
            }
        )
        artifacts.raw_stl.write_bytes(base64.b64decode(str(result["raw_stl_b64"])))
        raw_glb_b64 = result.get("raw_glb_b64")
        if raw_glb_b64:
            artifacts.raw_glb.write_bytes(base64.b64decode(str(raw_glb_b64)))
        else:
            write_placeholder_glb(artifacts.raw_glb)
        raw_manifest = result.get("manifest", {})
        real_manifest = raw_manifest if isinstance(raw_manifest, dict) else {}

    elapsed_seconds = round(time.monotonic() - start, 3)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "mode": "fast",
        "dry_run": dry_run,
        "prompt": prompt,
        "provider": "modal",
        "warmup": {
            "explicit": True,
            "performed": dry_run,
            "billable": not dry_run,
        },
        "target_size_mm": list(target_size_mm),
        "actual_size_mm": list(target_size_mm),
        "scale_factor": 1.0,
        "units": "mm",
        "elapsed_seconds": elapsed_seconds,
        "estimated_cost_usd": 0.0 if dry_run else max_cost_usd,
        "artifacts": {
            "source_image": str(artifacts.source_image),
            "cutout_image": str(artifacts.cutout_image),
            "raw_stl": str(artifacts.raw_stl),
            "raw_glb": str(artifacts.raw_glb),
            "manifest": str(artifacts.manifest),
        },
        **fast_model_manifest(),
    }
    if real_manifest:
        manifest.update(real_manifest)
        manifest["prompt"] = prompt
        manifest["artifacts"] = {
            "source_image": str(artifacts.source_image),
            "cutout_image": str(artifacts.cutout_image),
            "raw_stl": str(artifacts.raw_stl),
            "raw_glb": str(artifacts.raw_glb),
            "manifest": str(artifacts.manifest),
        }
    write_manifest(artifacts.manifest, manifest)
    return artifacts


def parse_target_size(value: str) -> tuple[float, float, float]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 3:
        raise ValueError("target size must contain exactly three comma-separated values.")
    try:
        x_mm, y_mm, z_mm = (float(part) for part in parts)
    except ValueError as error:
        raise ValueError("target size values must be numeric.") from error
    parsed = (x_mm, y_mm, z_mm)
    if any(part <= 0 for part in parsed):
        raise ValueError("target size values must be greater than zero.")
    return parsed
