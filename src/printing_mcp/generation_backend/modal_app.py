from __future__ import annotations

import argparse
import base64
import importlib
import json
import logging
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

ParseTargetSizeFn = Callable[[str], tuple[float, float, float]]
RunFastSmokeFn = Callable[..., Any]
CreateMeshFromBase64Fn = Callable[[dict[str, Any]], dict[str, Any]]
parse_target_size_fn: ParseTargetSizeFn | None = None
run_fast_smoke_fn: RunFastSmokeFn | None = None
create_mesh_from_base64_fn: CreateMeshFromBase64Fn | None = None
standalone_import_error: ModuleNotFoundError | None = None
DEFAULT_SMOKE_OUTPUT_DIR = str(Path(tempfile.gettempdir()) / "printing-generation-smoke")  # nosec B108
LOGGER = logging.getLogger(__name__)

if __package__ in {None, ""}:
    resolved_path = Path(__file__).resolve()
    if len(resolved_path.parents) >= 3:
        sys.path.append(str(resolved_path.parents[2]))
    try:
        from printing_mcp.generation_backend.mesh_runner import (
            create_mesh_from_base64 as imported_create_mesh_from_base64,
        )
        from printing_mcp.generation_backend.smoke import (
            parse_target_size as imported_parse_target_size,
        )
        from printing_mcp.generation_backend.smoke import run_fast_smoke as imported_run_fast_smoke

        parse_target_size_fn = imported_parse_target_size
        run_fast_smoke_fn = imported_run_fast_smoke
        create_mesh_from_base64_fn = imported_create_mesh_from_base64
    except ModuleNotFoundError as error:
        LOGGER.debug("Standalone modal_app import fallback failed: %s", error)
        standalone_import_error = error
else:
    from .mesh_runner import (
        create_mesh_from_base64 as imported_create_mesh_from_base64,
    )
    from .smoke import parse_target_size as imported_parse_target_size
    from .smoke import run_fast_smoke as imported_run_fast_smoke

    parse_target_size_fn = imported_parse_target_size
    run_fast_smoke_fn = imported_run_fast_smoke
    create_mesh_from_base64_fn = imported_create_mesh_from_base64

APP_NAME = "printing-generation"
HF_CACHE_VOLUME = "printing-generation-hf-cache"
TORCH_CACHE_VOLUME = "printing-generation-torch-cache"
GITHUB_CACHE_VOLUME = "printing-generation-github-cache"
LOCAL_SRC_ROOT = (
    Path(__file__).resolve().parents[3] if len(Path(__file__).resolve().parents) >= 4 else None
)
try:
    modal_module: Any | None = importlib.import_module("modal")
except ImportError:  # pragma: no cover - exercised only when optional dep is absent.
    modal_module = None

modal_app_factory: Any = modal_module.App if modal_module is not None else None

app = modal_app_factory(APP_NAME) if modal_app_factory is not None else None

if modal_module is not None:
    modal = modal_module
    gpu_image_base = (
        modal.Image.from_registry(
            "nvidia/cuda:12.8.1-devel-ubuntu22.04",
            add_python="3.11",
        )
        .apt_install("git", "build-essential", "clang", "libgl1", "libglib2.0-0")
        .env({"CC": "gcc", "CXX": "g++"})
        .pip_install(
            "huggingface-hub>=0.23.0",
            "numpy<2",
            "onnxruntime-gpu",
            "pillow>=10.0.0",
            "setuptools>=69.0.0",
            "torch",
            "torchvision",
            "trimesh>=4.0.0",
        )
        .pip_install(
            "git+https://github.com/tatsy/torchmcubes.git",
        )
        .run_commands(
            "git clone --depth 1 https://github.com/VAST-AI-Research/TripoSR.git /opt/TripoSR",
            "python -m pip install -r /opt/TripoSR/requirements.txt",
        )
        .env(
            {
                "HF_HOME": "/cache/huggingface",
                "PYTHONPATH": "/app/src:/opt/TripoSR",
                "TORCH_HOME": "/cache/torch",
                "XDG_CACHE_HOME": "/cache",
            }
        )
    )
    gpu_image = (
        gpu_image_base.add_local_dir(str(LOCAL_SRC_ROOT), remote_path="/app/src")
        if LOCAL_SRC_ROOT is not None
        else gpu_image_base
    )
    hf_cache_volume = modal.Volume.from_name(HF_CACHE_VOLUME, create_if_missing=True)
    torch_cache_volume = modal.Volume.from_name(TORCH_CACHE_VOLUME, create_if_missing=True)
    github_cache_volume = modal.Volume.from_name(GITHUB_CACHE_VOLUME, create_if_missing=True)
    gpu_volumes = {
        "/cache/huggingface": hf_cache_volume,
        "/cache/torch": torch_cache_volume,
        "/cache/github": github_cache_volume,
    }
    gpu_secrets = [modal.Secret.from_name("printing-huggingface")]
else:
    gpu_image = None
    gpu_volumes = {}
    gpu_secrets = []

if app is not None:

    @app.function(
        image=gpu_image,
        gpu="A10G",
        timeout=900,
        scaledown_window=300,
        volumes=cast(Any, gpu_volumes),
        secrets=gpu_secrets,
    )
    def create_mesh(request: dict[str, Any]) -> dict[str, Any]:
        return _create_mesh_from_base64_remote(request)

    @app.function(
        image=gpu_image,
        gpu="A10G",
        timeout=900,
        scaledown_window=300,
        volumes=cast(Any, gpu_volumes),
        secrets=gpu_secrets,
    )
    def warm_models(request: dict[str, Any]) -> dict[str, Any]:

        _ = request
        return {"status": "ready", "models": ["triposr"]}

    @app.function(
        image=gpu_image,
        gpu="A10G",
        timeout=900,
        scaledown_window=300,
        volumes=cast(Any, gpu_volumes),
        secrets=gpu_secrets,
    )
    def create_mesh_smoke(request: dict[str, Any]) -> dict[str, Any]:
        output_dir = Path(str(request["output_dir"]))
        output_dir.mkdir(parents=True, exist_ok=True)
        source_image = output_dir / "smoke_source.png"
        cutout_image = output_dir / "smoke_cutout.png"
        raw_stl = output_dir / "smoke_raw.stl"
        raw_glb = output_dir / "smoke_raw.glb"
        manifest_path = output_dir / "smoke_manifest.json"
        _write_placeholder_png_remote(source_image)
        cutout_image.write_bytes(source_image.read_bytes())
        result = _create_mesh_from_base64_remote(
            {
                "image_b64": base64.b64encode(cutout_image.read_bytes()).decode("ascii"),
                "target_size_mm": request["target_size_mm"],
                "max_cost_usd": request["max_cost_usd"],
                "seed": request.get("seed"),
            }
        )
        raw_stl.write_bytes(base64.b64decode(str(result["raw_stl_b64"])))
        raw_glb.write_bytes(base64.b64decode(str(result["raw_glb_b64"])))
        manifest = result.get("manifest", {})
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        return {
            "source_image": str(source_image),
            "cutout_image": str(cutout_image),
            "raw_stl": str(raw_stl),
            "raw_glb": str(raw_glb),
            "manifest": str(manifest_path),
            "manifest_payload": manifest,
        }

    @app.local_entrypoint()
    def modal_smoke(
        mode: str = "fast",
        prompt: str = "single small rounded gecko cave hide, one object, white background",
        target_size_mm: str = "80,60,45",
        output_dir: str = DEFAULT_SMOKE_OUTPUT_DIR,
        max_cost_usd: float = 0.25,
        real: bool = False,
    ) -> None:
        run_smoke_cli(
            mode=mode,
            prompt=prompt,
            target_size_mm=target_size_mm,
            output_dir=output_dir,
            max_cost_usd=max_cost_usd,
            real=real,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the printing generation backend smoke test.")
    parser.add_argument("--mode", choices=["fast"], default="fast")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--target-size-mm", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-cost-usd", type=float, default=0.25)
    parser.add_argument(
        "--real",
        action="store_true",
        help="Reserved for real Modal inference; dry-run placeholder artifacts are default.",
    )
    args = parser.parse_args()

    run_smoke_cli(
        mode=args.mode,
        prompt=args.prompt,
        target_size_mm=args.target_size_mm,
        output_dir=args.output_dir,
        max_cost_usd=args.max_cost_usd,
        real=args.real,
    )


def run_smoke_cli(
    *,
    mode: str,
    prompt: str,
    target_size_mm: str,
    output_dir: str,
    max_cost_usd: float,
    real: bool,
) -> None:
    if mode != "fast":
        raise ValueError("Only fast mode is implemented.")
    parsed_target_size_mm = _parse_target_size(target_size_mm)
    if real:
        if app is None:
            raise RuntimeError("Modal is required for real mesh generation.")
        result = create_mesh_smoke.remote(
            {
                "prompt": prompt,
                "target_size_mm": list(parsed_target_size_mm),
                "output_dir": output_dir,
                "max_cost_usd": max_cost_usd,
            }
        )
        print(f"source_image={result['source_image']}")
        print(f"cutout_image={result['cutout_image']}")
        print(f"raw_stl={result['raw_stl']}")
        print(f"raw_glb={result['raw_glb']}")
        print(f"manifest={result['manifest']}")
        print(f"manifest_payload={json.dumps(result['manifest_payload'], sort_keys=True)}")
        return
    else:
        if run_fast_smoke_fn is None:
            raise RuntimeError("Dry-run smoke requires printing_mcp package imports.")
        artifacts = run_fast_smoke_fn(
            prompt=prompt,
            target_size_mm=parsed_target_size_mm,
            output_dir=Path(output_dir).expanduser().resolve(),
            max_cost_usd=max_cost_usd,
            dry_run=True,
        )

    print(f"source_image={artifacts.source_image}")
    print(f"cutout_image={artifacts.cutout_image}")
    print(f"raw_stl={artifacts.raw_stl}")
    print(f"raw_glb={artifacts.raw_glb}")
    print(f"manifest={artifacts.manifest}")


def _parse_target_size(value: str) -> tuple[float, float, float]:
    if parse_target_size_fn is not None:
        return parse_target_size_fn(value)
    if standalone_import_error is not None:
        raise RuntimeError(
            "Could not import printing_mcp smoke helpers."
        ) from standalone_import_error
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 3:
        raise ValueError("target size must contain exactly three comma-separated values.")
    parsed = (float(parts[0]), float(parts[1]), float(parts[2]))
    if any(part <= 0 for part in parsed):
        raise ValueError("target size values must be greater than zero.")
    return parsed


def _write_placeholder_png_remote(path: Path) -> None:
    from PIL import Image

    image = Image.new("RGBA", (64, 64), (255, 255, 255, 255))
    image.save(path, format="PNG")


def _create_mesh_from_base64_remote(request: dict[str, Any]) -> dict[str, Any]:
    if create_mesh_from_base64_fn is None:
        raise RuntimeError(
            "TripoSR backend runner is not importable in the Modal environment."
        ) from standalone_import_error
    return create_mesh_from_base64_fn(request)


if __name__ == "__main__":
    main()
