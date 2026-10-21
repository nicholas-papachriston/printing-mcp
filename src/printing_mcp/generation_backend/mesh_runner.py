from __future__ import annotations

import base64
import json
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .model_versions import fast_model_manifest

# Stdlib-only so the runner imports in the Modal image and the local GPU Python.
MeshProvider = Literal["modal", "local"]


@dataclass(frozen=True)
class MeshRequest:
    image_b64: str
    target_size_mm: tuple[float, float, float]
    max_cost_usd: float
    seed: int | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> MeshRequest:
        image_b64 = payload.get("image_b64")
        if not isinstance(image_b64, str) or not image_b64:
            raise ValueError("image_b64 is required.")
        raw_target = payload.get("target_size_mm")
        if not isinstance(raw_target, list | tuple) or len(raw_target) != 3:
            raise ValueError("target_size_mm must contain exactly three values.")
        raw_seed = payload.get("seed")
        return cls(
            image_b64=image_b64,
            target_size_mm=(float(raw_target[0]), float(raw_target[1]), float(raw_target[2])),
            max_cost_usd=float(payload.get("max_cost_usd", 0.08)),
            seed=int(raw_seed) if raw_seed is not None else None,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "image_b64": self.image_b64,
            "target_size_mm": list(self.target_size_mm),
            "max_cost_usd": self.max_cost_usd,
            "seed": self.seed,
        }


@dataclass(frozen=True)
class MeshResult:
    raw_stl_b64: str
    raw_glb_b64: str
    manifest: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> MeshResult:
        raw_stl_b64 = payload.get("raw_stl_b64")
        raw_glb_b64 = payload.get("raw_glb_b64")
        manifest = payload.get("manifest", {})
        if not isinstance(raw_stl_b64, str) or not raw_stl_b64:
            raise ValueError("raw_stl_b64 is required in mesh response.")
        if not isinstance(raw_glb_b64, str):
            raise ValueError("raw_glb_b64 is required in mesh response.")
        return cls(
            raw_stl_b64=raw_stl_b64,
            raw_glb_b64=raw_glb_b64,
            manifest=manifest if isinstance(manifest, dict) else {},
        )

    @classmethod
    def from_artifacts(cls, artifacts: MeshArtifacts) -> MeshResult:
        return cls(
            raw_stl_b64=base64.b64encode(artifacts.raw_stl).decode("ascii"),
            raw_glb_b64=base64.b64encode(artifacts.raw_glb or b"").decode("ascii"),
            manifest=artifacts.manifest,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "raw_stl_b64": self.raw_stl_b64,
            "raw_glb_b64": self.raw_glb_b64,
            "manifest": self.manifest,
        }


@dataclass(frozen=True)
class MeshArtifacts:
    raw_stl: bytes
    raw_glb: bytes | None
    manifest: dict[str, Any]


def create_mesh(
    *,
    image_bytes: bytes,
    target_size_mm: tuple[float, float, float],
    max_cost_usd: float,
    seed: int | None = None,
    provider: MeshProvider = "modal",
) -> MeshArtifacts:
    if max_cost_usd <= 0:
        raise ValueError("max_cost_usd must be greater than zero.")
    if any(value <= 0 for value in target_size_mm):
        raise ValueError("target_size_mm values must be greater than zero.")

    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="printing-mesh-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        input_path = temp_dir / "input.png"
        raw_glb_path = temp_dir / "mesh.glb"
        raw_stl_path = temp_dir / "mesh.stl"
        input_path.write_bytes(image_bytes)

        mesh, device = _run_triposr(input_path=input_path, seed=seed)
        mesh = _normalize_mesh(mesh, target_size_mm)
        mesh.export(raw_stl_path)
        _to_gltf_axes(mesh.copy()).export(raw_glb_path)

        bounds = mesh.bounds
        actual_size_mm = (bounds[1] - bounds[0]).tolist()
        triangle_count = int(len(mesh.faces))
        if triangle_count <= 12:
            raise ValueError("mesh_empty: TripoSR produced an unexpectedly small mesh.")

        elapsed_seconds = round(time.monotonic() - start, 3)
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "mode": "fast",
            "dry_run": False,
            "provider": provider,
            "device": device,
            "seed": seed,
            "target_size_mm": list(target_size_mm),
            "actual_size_mm": [round(float(value), 4) for value in actual_size_mm],
            "scale_factor": float(mesh.metadata.get("printing_scale_factor", 1.0)),
            "units": "mm",
            "triangle_count": triangle_count,
            "elapsed_seconds": elapsed_seconds,
            "estimated_cost_usd": max_cost_usd if provider == "modal" else 0.0,
            **fast_model_manifest(),
        }
        return MeshArtifacts(
            raw_stl=raw_stl_path.read_bytes(),
            raw_glb=raw_glb_path.read_bytes(),
            manifest=manifest,
        )


def create_mesh_from_base64(
    request: dict[str, Any], *, provider: MeshProvider = "modal"
) -> dict[str, Any]:
    mesh_request = MeshRequest.from_payload(request)
    artifacts = create_mesh(
        image_bytes=base64.b64decode(mesh_request.image_b64),
        target_size_mm=mesh_request.target_size_mm,
        max_cost_usd=mesh_request.max_cost_usd,
        seed=mesh_request.seed,
        provider=provider,
    )
    return MeshResult.from_artifacts(artifacts).to_payload()


def _run_triposr(*, input_path: Path, seed: int | None) -> tuple[Any, str]:
    np = _import_required("numpy")
    torch = _import_required("torch")
    trimesh = _import_required("trimesh")
    from PIL import Image

    TSR = _import_required("tsr.system").TSR
    if seed is not None:
        torch.manual_seed(seed)
        np.random.seed(seed % (2**32 - 1))

    device = "cuda" if torch.cuda.is_available() else "cpu"
    image = Image.open(input_path).convert("RGB")
    model = TSR.from_pretrained(
        "stabilityai/TripoSR",
        config_name="config.yaml",
        weight_name="model.ckpt",
    )
    model.renderer.set_chunk_size(8192)
    model.to(device)
    with torch.no_grad():
        scene_codes = model([image], device=device)
        meshes = model.extract_mesh(scene_codes, has_vertex_color=False)
    mesh = meshes[0]
    if isinstance(mesh, trimesh.Scene):
        converted = mesh.dump(concatenate=True)
        if converted is None:
            raise ValueError("mesh_empty: TripoSR produced an empty scene.")
        mesh = converted
    if not isinstance(mesh, trimesh.Trimesh):
        mesh = trimesh.Trimesh(vertices=mesh.vertices, faces=mesh.faces, process=False)
    mesh.remove_unreferenced_vertices()
    return mesh, device


def _normalize_mesh(mesh: Any, target_size_mm: tuple[float, float, float]) -> Any:
    np = _import_required("numpy")

    if mesh.vertices.size == 0 or mesh.faces.size == 0:
        raise ValueError("mesh_empty: TripoSR produced no vertices or faces.")

    bounds = mesh.bounds
    size = bounds[1] - bounds[0]
    if np.any(size <= 0):
        raise ValueError("mesh_empty: TripoSR mesh has invalid bounds.")

    target = np.array(target_size_mm, dtype=float)
    scale_factor = float(np.min(target / size))
    mesh.apply_scale(scale_factor)

    bounds = mesh.bounds
    center_xy = (bounds[0][:2] + bounds[1][:2]) / 2.0
    translation = np.array([-center_xy[0], -center_xy[1], -bounds[0][2]])
    mesh.apply_translation(translation)
    mesh.metadata["printing_scale_factor"] = scale_factor
    return mesh


def _to_gltf_axes(mesh: Any) -> Any:
    """Rotate a Z-up print mesh to glTF's Y-up so GLB importers stand it upright."""
    np = _import_required("numpy")
    trimesh = _import_required("trimesh")

    mesh.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    return mesh


def _import_required(module_name: str) -> Any:
    import importlib

    try:
        return importlib.import_module(module_name)
    except ImportError as error:
        raise RuntimeError(f"Missing backend dependency: {module_name}") from error


def main(argv: list[str]) -> None:
    """Local GPU entry: read a mesh request JSON file, write the result JSON file."""
    request_path, result_path = (Path(arg) for arg in argv)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    result = create_mesh_from_base64(request, provider="local")
    result_path.write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
