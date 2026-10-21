from __future__ import annotations

import base64
import importlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, assert_never, cast

from ..config import (
    LOCAL_MESH_PYTHON_ENV,
    PROJECT_ROOT,
    TRIPOSR_DIR_ENV,
    load_local_env,
    local_env_value,
)
from ..generation_backend.artifacts import write_placeholder_png
from ..generation_backend.mesh_runner import MeshProvider, MeshRequest, MeshResult
from ..generation_backend.model_versions import IMAGE_MODEL_ID
from ..generation_backend.smoke import run_fast_smoke

DEFAULT_MODAL_APP = "printing-generation"
LOCAL_MESH_TIMEOUT_SECONDS = 900.0


@dataclass(frozen=True)
class ImageGenerationBackendResult:
    prompt: str
    negative_prompt: str | None
    seed: int | None
    width: int
    height: int
    output_path: Path
    model: str
    model_id: str
    image_backend: str
    dry_run: bool
    estimated_cost_usd: float

    def to_record(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "seed": self.seed,
            "width": self.width,
            "height": self.height,
            "output_path": str(self.output_path),
            "model": self.model,
            "model_id": self.model_id,
            "image_backend": self.image_backend,
            "dry_run": self.dry_run,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


@dataclass(frozen=True)
class MeshMetadata:
    units: str
    target_size_mm: object
    actual_size_mm: object
    scale_factor: object
    triangle_count: object

    def to_record(self) -> dict[str, Any]:
        return {
            "units": self.units,
            "target_size_mm": self.target_size_mm,
            "actual_size_mm": self.actual_size_mm,
            "scale_factor": self.scale_factor,
            "triangle_count": self.triangle_count,
        }


@dataclass(frozen=True)
class MeshGenerationBackendResult:
    source_image_path: Path
    output_raw_stl_path: Path
    output_raw_glb_path: Path | None
    mesh: MeshMetadata
    dry_run: bool
    mesh_model: str
    estimated_cost_usd: object
    mesh_model_id: object | None = None
    mesh_model_revision: object | None = None
    elapsed_seconds: object | None = None
    device: object | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "source_image_path": str(self.source_image_path),
            "output_raw_stl_path": str(self.output_raw_stl_path),
            "output_raw_glb_path": str(self.output_raw_glb_path)
            if self.output_raw_glb_path
            else None,
            "mesh": self.mesh.to_record(),
            "dry_run": self.dry_run,
            "mesh_model": self.mesh_model,
            "mesh_model_id": self.mesh_model_id,
            "mesh_model_revision": self.mesh_model_revision,
            "estimated_cost_usd": self.estimated_cost_usd,
            "elapsed_seconds": self.elapsed_seconds,
            "device": self.device,
        }


class GenerationBackendClient:
    def create_image(
        self,
        *,
        prompt: str,
        negative_prompt: str | None,
        seed: int | None,
        width: int,
        height: int,
        output_path: Path,
        max_cost_usd: float,
    ) -> ImageGenerationBackendResult:
        _ = max_cost_usd
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if _generation_dry_run_enabled():
            write_placeholder_png(output_path, width=width, height=height)
            image_backend = "placeholder"
            dry_run = True
        elif api_key := _google_api_key():
            _create_nano_banana_image(
                api_key=api_key,
                prompt=prompt,
                negative_prompt=negative_prompt,
                output_path=output_path,
                width=width,
                height=height,
            )
            image_backend = "google_genai"
            dry_run = False
        else:
            raise RuntimeError(
                "provider_auth_missing: set GEMINI_API_KEY/GOOGLE_API_KEY or enable "
                "PRINTING_GENERATION_DRY_RUN=1 for placeholder image output."
            )
        return ImageGenerationBackendResult(
            prompt=prompt,
            negative_prompt=negative_prompt,
            seed=seed,
            width=width,
            height=height,
            output_path=output_path,
            model="nano_banana_2",
            model_id=IMAGE_MODEL_ID,
            image_backend=image_backend,
            dry_run=dry_run,
            estimated_cost_usd=0.0,
        )

    def create_fast_mesh(
        self,
        *,
        prompt: str,
        source_image_path: Path,
        target_size_mm: tuple[float, float, float],
        output_raw_stl_path: Path,
        output_raw_glb_path: Path | None,
        max_cost_usd: float,
        provider: MeshProvider = "modal",
    ) -> MeshGenerationBackendResult:
        if _generation_dry_run_enabled():
            return self._create_dry_run_mesh(
                prompt=prompt,
                source_image_path=source_image_path,
                target_size_mm=target_size_mm,
                output_raw_stl_path=output_raw_stl_path,
                output_raw_glb_path=output_raw_glb_path,
                max_cost_usd=max_cost_usd,
            )

        request = MeshRequest(
            image_b64=base64.b64encode(source_image_path.read_bytes()).decode("ascii"),
            target_size_mm=target_size_mm,
            max_cost_usd=max_cost_usd,
        )
        match provider:
            case "modal":
                result = _call_modal_create_mesh(request)
            case "local":
                result = _call_local_create_mesh(request)
            case _:
                assert_never(provider)
        output_raw_stl_path.parent.mkdir(parents=True, exist_ok=True)
        output_raw_stl_path.write_bytes(base64.b64decode(result.raw_stl_b64))
        if output_raw_glb_path is not None and result.raw_glb_b64:
            output_raw_glb_path.parent.mkdir(parents=True, exist_ok=True)
            output_raw_glb_path.write_bytes(base64.b64decode(result.raw_glb_b64))
        mesh_manifest = cast(dict[str, object], result.manifest)
        return MeshGenerationBackendResult(
            source_image_path=source_image_path,
            output_raw_stl_path=output_raw_stl_path,
            output_raw_glb_path=output_raw_glb_path,
            mesh=MeshMetadata(
                units="mm",
                target_size_mm=mesh_manifest.get("target_size_mm", list(target_size_mm)),
                actual_size_mm=mesh_manifest.get("actual_size_mm", list(target_size_mm)),
                scale_factor=mesh_manifest.get("scale_factor", 1.0),
                triangle_count=mesh_manifest.get("triangle_count", 0),
            ),
            dry_run=False,
            mesh_model=str(mesh_manifest.get("mesh_model", "triposr")),
            mesh_model_id=mesh_manifest.get("mesh_model_id"),
            mesh_model_revision=mesh_manifest.get("mesh_model_revision"),
            estimated_cost_usd=mesh_manifest.get("estimated_cost_usd", max_cost_usd),
            elapsed_seconds=mesh_manifest.get("elapsed_seconds"),
            device=mesh_manifest.get("device"),
        )

    def _create_dry_run_mesh(
        self,
        *,
        prompt: str,
        source_image_path: Path,
        target_size_mm: tuple[float, float, float],
        output_raw_stl_path: Path,
        output_raw_glb_path: Path | None,
        max_cost_usd: float,
    ) -> MeshGenerationBackendResult:
        with tempfile.TemporaryDirectory(prefix="printing-generation-mesh-") as temp_dir:
            artifacts = run_fast_smoke(
                prompt=prompt,
                target_size_mm=target_size_mm,
                output_dir=Path(temp_dir),
                max_cost_usd=max_cost_usd,
                dry_run=True,
            )
            output_raw_stl_path.parent.mkdir(parents=True, exist_ok=True)
            output_raw_stl_path.write_bytes(artifacts.raw_stl.read_bytes())
            if output_raw_glb_path is not None:
                output_raw_glb_path.parent.mkdir(parents=True, exist_ok=True)
                output_raw_glb_path.write_bytes(artifacts.raw_glb.read_bytes())

        return MeshGenerationBackendResult(
            source_image_path=source_image_path,
            output_raw_stl_path=output_raw_stl_path,
            output_raw_glb_path=output_raw_glb_path,
            mesh=MeshMetadata(
                units="mm",
                target_size_mm=list(target_size_mm),
                actual_size_mm=list(target_size_mm),
                scale_factor=1.0,
                triangle_count=12,
            ),
            dry_run=True,
            mesh_model="triposr",
            estimated_cost_usd=0.0,
        )


def _google_api_key() -> str | None:
    load_local_env()
    return (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or local_env_value("GEMINI_API_KEY")
        or local_env_value("GOOGLE_API_KEY")
    )


def _generation_dry_run_enabled() -> bool:
    load_local_env()
    return os.environ.get("PRINTING_GENERATION_DRY_RUN", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _modal_app_name() -> str:
    load_local_env()
    return (
        os.environ.get("PRINTING_GENERATION_MODAL_APP")
        or local_env_value("PRINTING_GENERATION_MODAL_APP")
        or DEFAULT_MODAL_APP
    )


def _call_modal_create_mesh(request: MeshRequest) -> MeshResult:
    load_local_env()
    try:
        modal_module = importlib.import_module("modal")
    except ImportError as error:
        raise RuntimeError("modal is required for real mesh generation.") from error

    create_mesh = modal_module.Function.from_name(_modal_app_name(), "create_mesh")
    result = create_mesh.remote(request.to_payload())
    if not isinstance(result, dict):
        raise RuntimeError("Modal create_mesh returned an invalid response.")
    return MeshResult.from_payload(result)


def _call_local_create_mesh(request: MeshRequest) -> MeshResult:
    """Run mesh_runner in the local GPU Python, the same code Modal runs in its image."""
    python = local_env_value(LOCAL_MESH_PYTHON_ENV)
    triposr_dir = local_env_value(TRIPOSR_DIR_ENV)
    if not python or not triposr_dir:
        raise RuntimeError(
            f"Set {LOCAL_MESH_PYTHON_ENV} and {TRIPOSR_DIR_ENV} for provider='local'."
        )
    with tempfile.TemporaryDirectory(prefix="printing-local-mesh-") as temp_dir:
        request_path = Path(temp_dir) / "request.json"
        result_path = Path(temp_dir) / "result.json"
        request_path.write_text(json.dumps(request.to_payload()), encoding="utf-8")
        env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join([str(PROJECT_ROOT / "src"), triposr_dir]),
        }
        try:
            completed = subprocess.run(
                [
                    python,
                    "-m",
                    "printing_mcp.generation_backend.mesh_runner",
                    str(request_path),
                    str(result_path),
                ],
                env=env,
                # The MCP server's stdin is the JSON-RPC pipe; a child that inherits it
                # blocks at startup on Windows while the server has a read pending.
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=LOCAL_MESH_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                f"Local TripoSR ran past {LOCAL_MESH_TIMEOUT_SECONDS:g} s."
            ) from error
        if completed.returncode != 0 or not result_path.is_file():
            tail = "\n".join(completed.stderr.strip().splitlines()[-5:])
            raise RuntimeError(f"Local TripoSR failed (exit {completed.returncode}): {tail}")
        return MeshResult.from_payload(json.loads(result_path.read_text(encoding="utf-8")))


def _create_nano_banana_image(
    *,
    api_key: str,
    prompt: str,
    negative_prompt: str | None,
    output_path: Path,
    width: int,
    height: int,
) -> None:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    full_prompt = prompt
    if negative_prompt:
        full_prompt = f"{prompt}\nAvoid: {negative_prompt}"
    response = client.models.generate_content(
        model=IMAGE_MODEL_ID,
        contents=[full_prompt],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio=_image_aspect_ratio(width, height), image_size="1K"
            ),
        ),
    )
    image_bytes = _first_inline_image(response)
    if image_bytes is None:
        raise ValueError("Nano Banana 2 response did not include image bytes.")
    _write_png(image_bytes, output_path, width=width, height=height)


def _first_inline_image(response: Any) -> bytes | None:
    for candidate in response.candidates or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            inline_data = getattr(part, "inline_data", None)
            data = getattr(inline_data, "data", None)
            if data:
                return bytes(data)
    return None


def _write_png(image_bytes: bytes, output_path: Path, *, width: int, height: int) -> None:
    from PIL import Image

    with Image.open(BytesIO(image_bytes)) as image:
        if image.size != (width, height):
            image = image.resize((width, height))
        image.save(output_path, format="PNG")


def _image_aspect_ratio(width: int, height: int) -> str:
    if width == height:
        return "1:1"
    return "16:9" if width > height else "9:16"
