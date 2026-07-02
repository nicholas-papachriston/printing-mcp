from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def version_dir(workspace_root: Path, model_name: str, version: int) -> Path:
    return workspace_root / model_name / f"v{version}"


def generation_dir(workspace_root: Path, model_name: str, version: int) -> Path:
    return version_dir(workspace_root, model_name, version) / "generation"


def resolve_generation_path(
    workspace_root: Path,
    model_name: str,
    version: int,
    value: str,
    *,
    label: str,
) -> Path:
    resolved = Path(value).expanduser().resolve()
    allowed_root = generation_dir(workspace_root, model_name, version).resolve()
    if resolved != allowed_root and allowed_root not in resolved.parents:
        raise ValueError(f"{label} must be inside {allowed_root}: {resolved}")
    return resolved


def default_record_path(workspace_root: Path, model_name: str, version: int) -> Path:
    return generation_dir(workspace_root, model_name, version) / "generation.json"


def resolve_generation_record_path(
    workspace_root: Path,
    model_name: str,
    version: int,
    record_path: str | None,
) -> Path:
    if record_path is None:
        return default_record_path(workspace_root, model_name, version)
    return resolve_generation_path(
        workspace_root,
        model_name,
        version,
        record_path,
        label="record_path",
    )


def append_generation_call(
    record_path: Path,
    *,
    model_name: str,
    version: int,
    tool_name: str,
    status: str,
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    error: dict[str, Any] | None = None,
    provider: str = "local",
) -> None:
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record = _read_record(record_path)
    record.setdefault("schema_version", 1)
    record.setdefault("model_name", model_name)
    record.setdefault("version", version)
    record.setdefault("artifacts", {})
    calls = record.setdefault("calls", [])
    if not isinstance(calls, list):
        raise ValueError(f"generation record calls must be a list: {record_path}")

    calls.append(
        {
            "tool_name": tool_name,
            "status": status,
            "started_at": datetime.now(UTC).isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "provider": provider,
            "inputs": inputs,
            "outputs": outputs,
            "error": error,
        }
    )

    temp_path = record_path.with_suffix(record_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(record_path)


def _read_record(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"generation record is not valid JSON: {path}") from error
    if not isinstance(data, dict):
        raise ValueError(f"generation record must be a JSON object: {path}")
    return data
