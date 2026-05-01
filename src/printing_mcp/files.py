from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from .types import DiscoveredFile, JsonObject, PathLike

DISCOVERED_SUFFIXES = {
    ".3mf": "3mf",
    ".bgcode": "bgcode",
    ".gcode": "gcode",
    ".json": "json",
    ".stl": "stl",
}


def resolve_existing_file(path: PathLike, *, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(
            f"{label} does not exist or is not a file: {resolved}. "
            "Pass an absolute path to an STL, 3MF, OBJ, or OrcaSlicer JSON settings file."
        )
    return resolved


def resolve_existing_files(paths: list[str], *, label: str) -> list[Path]:
    return [
        resolve_existing_file(path, label=f"{label}[{index}]") for index, path in enumerate(paths)
    ]


def resolve_output_dir(path: PathLike) -> Path:
    resolved = Path(path).expanduser().resolve()
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ValueError(f"Could not create output directory {resolved}: {error}") from error
    if not resolved.is_dir():
        raise ValueError(
            f"Output path is not a directory: {resolved}. "
            "Choose a directory path where OrcaSlicer can write generated files."
        )
    return resolved


def resolve_parent_for_output(path: PathLike) -> Path:
    resolved = Path(path).expanduser().resolve()
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ValueError(f"Could not create parent directory for {resolved}: {error}") from error
    return resolved


def read_json_file(path: Path) -> JsonObject | None:
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as file:
            data: object = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    return cast(JsonObject, data) if isinstance(data, dict) else None


def discover_files(paths: list[Path]) -> list[DiscoveredFile]:
    discovered: list[DiscoveredFile] = []
    seen: set[Path] = set()

    for path in paths:
        if path.is_file():
            candidates = [path]
        elif path.is_dir():
            candidates = [
                child
                for child in path.rglob("*")
                if child.is_file() and child.suffix.lower() in DISCOVERED_SUFFIXES
            ]
        else:
            candidates = []

        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            suffix = resolved.suffix.lower()
            discovered.append(
                DiscoveredFile(
                    path=str(resolved),
                    kind=DISCOVERED_SUFFIXES.get(suffix, suffix.lstrip(".") or "file"),
                    size_bytes=resolved.stat().st_size,
                )
            )

    return sorted(discovered, key=lambda item: item.path)
