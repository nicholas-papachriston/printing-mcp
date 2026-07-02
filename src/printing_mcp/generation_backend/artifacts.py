from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SmokeArtifacts:
    source_image: Path
    cutout_image: Path
    raw_stl: Path
    raw_glb: Path
    manifest: Path


def ensure_output_dir(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)


def write_placeholder_png(path: Path, *, width: int = 1, height: int = 1) -> None:
    from PIL import Image

    image = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    image.save(path, format="PNG")


def write_placeholder_stl(path: Path, target_size_mm: tuple[float, float, float]) -> None:
    x_mm, y_mm, z_mm = target_size_mm
    vertices = [
        (0.0, 0.0, 0.0),
        (x_mm, 0.0, 0.0),
        (x_mm, y_mm, 0.0),
        (0.0, y_mm, 0.0),
        (0.0, 0.0, z_mm),
        (x_mm, 0.0, z_mm),
        (x_mm, y_mm, z_mm),
        (0.0, y_mm, z_mm),
    ]
    faces = [
        (0, 2, 1),
        (0, 3, 2),
        (4, 5, 6),
        (4, 6, 7),
        (0, 1, 5),
        (0, 5, 4),
        (1, 2, 6),
        (1, 6, 5),
        (2, 3, 7),
        (2, 7, 6),
        (3, 0, 4),
        (3, 4, 7),
    ]

    lines = ["solid printing_generation_smoke"]
    for face in faces:
        normal = _normal(vertices[face[0]], vertices[face[1]], vertices[face[2]])
        lines.append(f"  facet normal {normal[0]:.6f} {normal[1]:.6f} {normal[2]:.6f}")
        lines.append("    outer loop")
        for index in face:
            vertex = vertices[index]
            lines.append(f"      vertex {vertex[0]:.6f} {vertex[1]:.6f} {vertex[2]:.6f}")
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append("endsolid printing_generation_smoke")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_placeholder_glb(path: Path) -> None:
    path.write_bytes(b"glTF\x02\x00\x00\x00\x0c\x00\x00\x00")


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _normal(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
    c: tuple[float, float, float],
) -> tuple[float, float, float]:
    ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    ac = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    cross = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    length = math.sqrt(cross[0] ** 2 + cross[1] ** 2 + cross[2] ** 2)
    if length == 0:
        return (0.0, 0.0, 0.0)
    return (cross[0] / length, cross[1] / length, cross[2] / length)
