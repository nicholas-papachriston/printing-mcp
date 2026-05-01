from __future__ import annotations

import base64
import importlib
import os
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any, cast

from ..config import load_local_env, local_env_value
from ..generation_backend.artifacts import write_placeholder_png
from ..generation_backend.model_versions import IMAGE_MODEL_ID
from ..generation_backend.smoke import run_fast_smoke

DEFAULT_MODAL_APP = "printing-generation"


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
    ) -> dict[str, object]:
        _ = max_cost_usd
        output_path.parent.mkdir(parents=True, exist_ok=True)
        api_key = _google_api_key()
        if api_key:
            _create_nano_banana_image(
                api_key=api_key,
                prompt=prompt,
                negative_prompt=negative_prompt,
                output_path=output_path,
            )
            image_backend = "google_genai"
            dry_run = False
        else:
            write_placeholder_png(output_path)
            image_backend = "placeholder"
            dry_run = True
        return {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "seed": seed,
            "width": width,
            "height": height,
            "output_path": str(output_path),
            "model": "nano_banana_2",
            "model_id": IMAGE_MODEL_ID,
            "image_backend": image_backend,
            "dry_run": dry_run,
            "estimated_cost_usd": 0.0,
        }

    def create_fast_mesh(
        self,
        *,
        prompt: str,
        source_image_path: Path,
        target_size_mm: tuple[float, float, float],
        output_raw_stl_path: Path,
        output_raw_glb_path: Path | None,
        max_cost_usd: float,
    ) -> dict[str, object]:
        if _generation_dry_run_enabled():
            return self._create_dry_run_mesh(
                prompt=prompt,
                source_image_path=source_image_path,
                target_size_mm=target_size_mm,
                output_raw_stl_path=output_raw_stl_path,
                output_raw_glb_path=output_raw_glb_path,
                max_cost_usd=max_cost_usd,
            )

        result = _call_modal_create_mesh(
            image_bytes=source_image_path.read_bytes(),
            target_size_mm=target_size_mm,
            max_cost_usd=max_cost_usd,
        )
        output_raw_stl_path.parent.mkdir(parents=True, exist_ok=True)
        output_raw_stl_path.write_bytes(base64.b64decode(str(result["raw_stl_b64"])))
        raw_glb_b64 = result.get("raw_glb_b64")
        if output_raw_glb_path is not None and raw_glb_b64:
            output_raw_glb_path.parent.mkdir(parents=True, exist_ok=True)
            output_raw_glb_path.write_bytes(base64.b64decode(str(raw_glb_b64)))
        manifest = result.get("manifest", {})
        mesh_manifest = cast(dict[str, object], manifest) if isinstance(manifest, dict) else {}
        return {
            "source_image_path": str(source_image_path),
            "output_raw_stl_path": str(output_raw_stl_path),
            "output_raw_glb_path": str(output_raw_glb_path) if output_raw_glb_path else None,
            "mesh": {
                "units": "mm",
                "target_size_mm": mesh_manifest.get("target_size_mm", list(target_size_mm)),
                "actual_size_mm": mesh_manifest.get("actual_size_mm", list(target_size_mm)),
                "scale_factor": mesh_manifest.get("scale_factor", 1.0),
                "triangle_count": mesh_manifest.get("triangle_count", 0),
            },
            "dry_run": False,
            "mesh_model": mesh_manifest.get("mesh_model", "triposr"),
            "mesh_model_id": mesh_manifest.get("mesh_model_id"),
            "mesh_model_revision": mesh_manifest.get("mesh_model_revision"),
            "estimated_cost_usd": mesh_manifest.get("estimated_cost_usd", max_cost_usd),
            "elapsed_seconds": mesh_manifest.get("elapsed_seconds"),
        }

    def _create_dry_run_mesh(
        self,
        *,
        prompt: str,
        source_image_path: Path,
        target_size_mm: tuple[float, float, float],
        output_raw_stl_path: Path,
        output_raw_glb_path: Path | None,
        max_cost_usd: float,
    ) -> dict[str, object]:
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

        return {
            "source_image_path": str(source_image_path),
            "output_raw_stl_path": str(output_raw_stl_path),
            "output_raw_glb_path": str(output_raw_glb_path) if output_raw_glb_path else None,
            "mesh": {
                "units": "mm",
                "target_size_mm": list(target_size_mm),
                "actual_size_mm": list(target_size_mm),
                "scale_factor": 1.0,
                "triangle_count": 12,
            },
            "dry_run": True,
            "mesh_model": "triposr",
            "estimated_cost_usd": 0.0,
        }


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


def _call_modal_create_mesh(
    *,
    image_bytes: bytes,
    target_size_mm: tuple[float, float, float],
    max_cost_usd: float,
) -> dict[str, object]:
    load_local_env()
    try:
        modal_module = importlib.import_module("modal")
    except ImportError as error:
        raise RuntimeError("modal is required for real mesh generation.") from error

    create_mesh = modal_module.Function.from_name(_modal_app_name(), "create_mesh")
    result = create_mesh.remote(
        {
            "image_b64": base64.b64encode(image_bytes).decode("ascii"),
            "target_size_mm": list(target_size_mm),
            "max_cost_usd": max_cost_usd,
        }
    )
    if not isinstance(result, dict):
        raise RuntimeError("Modal create_mesh returned an invalid response.")
    return result


def _create_nano_banana_image(
    *,
    api_key: str,
    prompt: str,
    negative_prompt: str | None,
    output_path: Path,
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
            image_config=types.ImageConfig(aspect_ratio="1:1", image_size="1K"),
        ),
    )
    image_bytes = _first_inline_image(response)
    if image_bytes is None:
        raise ValueError("Nano Banana 2 response did not include image bytes.")
    _write_png(image_bytes, output_path)


def _first_inline_image(response: Any) -> bytes | None:
    for candidate in response.candidates or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            inline_data = getattr(part, "inline_data", None)
            data = getattr(inline_data, "data", None)
            if data:
                return bytes(data)
    return None


def _write_png(image_bytes: bytes, output_path: Path) -> None:
    from PIL import Image

    with Image.open(BytesIO(image_bytes)) as image:
        image.save(output_path, format="PNG")
