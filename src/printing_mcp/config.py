from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_TIMEOUT_SECONDS = 900.0
DEFAULT_BLENDER_HOST = "localhost"
DEFAULT_BLENDER_PORT = 9876
DEFAULT_BLENDER_TIMEOUT_SECONDS = 180.0
DEFAULT_ELEGOO_TIMEOUT_SECONDS = 30.0
DEFAULT_ELEGOO_DISCOVERY_TIMEOUT_SECONDS = 8.0
DEFAULT_ELEGOO_UPLOAD_CHUNK_BYTES = 1024 * 1024
LOCAL_MESH_PYTHON_ENV = "PRINTING_LOCAL_MESH_PYTHON"
TRIPOSR_DIR_ENV = "PRINTING_TRIPOSR_DIR"
ORCA_PATH_COMMANDS = (
    "orca-slicer-console",
    "orca-slicer",
    "OrcaSlicer",
    "orcaslicer",
)
WINDOWS_ORCA_EXECUTABLES = (
    "orca-slicer-console.exe",
    "orca-slicer.exe",
    "OrcaSlicer.exe",
    "orcaslicer.exe",
)
MACOS_ORCA_EXECUTABLES = (
    "Contents/MacOS/OrcaSlicer",
    "Contents/MacOS/orca-slicer",
    "OrcaSlicer",
    "orca-slicer",
    "orcaslicer",
)
LINUX_ORCA_EXECUTABLES = (
    "orca-slicer",
    "OrcaSlicer",
    "orcaslicer",
    "OrcaSlicer.AppImage",
)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
_LOADED_ENV_PATH: Path | None = None


class BlenderConfig(BaseModel):
    host: str = DEFAULT_BLENDER_HOST
    port: int = Field(default=DEFAULT_BLENDER_PORT, ge=1, le=65535)
    timeout_seconds: float = Field(default=DEFAULT_BLENDER_TIMEOUT_SECONDS, gt=0)
    config_issue: str | None = None


class ElegooConfig(BaseModel):
    host: str | None = None
    access_code: str | None = None
    use_default_access_code: bool = False
    serial_number: str | None = None
    mainboard_id: str | None = None
    printer_type: str | None = None
    timeout_seconds: float = Field(default=DEFAULT_ELEGOO_TIMEOUT_SECONDS, gt=0)
    discovery_timeout_seconds: float = Field(
        default=DEFAULT_ELEGOO_DISCOVERY_TIMEOUT_SECONDS,
        gt=0,
    )
    upload_chunk_bytes: int = Field(default=DEFAULT_ELEGOO_UPLOAD_CHUNK_BYTES, gt=0)
    config_issue: str | None = None


class OrcaConfig(BaseModel):
    binary_path: Path | None = None
    datadir: Path | None = None
    timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0)
    explicit_binary: bool = False
    binary_issue: str | None = None

    @property
    def available(self) -> bool:
        return self.binary_path is not None and self.binary_path.is_file()


class PrintingMcpConfig(BaseModel):
    blender: BlenderConfig
    elegoo: ElegooConfig
    orca: OrcaConfig


def load_local_env(start_dir: Path | None = None, *, override: bool = False) -> Path | None:
    """Load the nearest repo .env into os.environ without overwriting existing values."""

    global _LOADED_ENV_PATH
    for env_path in _candidate_env_paths(start_dir or Path.cwd()):
        if env_path.is_file():
            _load_env_file(env_path, override=override)
            _LOADED_ENV_PATH = env_path.resolve()
            return _LOADED_ENV_PATH
    return _LOADED_ENV_PATH


def loaded_env_path() -> Path | None:
    return _LOADED_ENV_PATH


def local_env_value(name: str) -> str | None:
    load_local_env()
    return os.environ.get(name)


def modal_auth_configured() -> bool:
    load_local_env()
    return bool(os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"))


def local_mesh_configured() -> bool:
    load_local_env()
    return bool(os.environ.get(LOCAL_MESH_PYTHON_ENV) and os.environ.get(TRIPOSR_DIR_ENV))


def load_printing_config() -> PrintingMcpConfig:
    return PrintingMcpConfig(
        blender=load_blender_config(),
        elegoo=load_elegoo_config(),
        orca=load_orca_config(),
    )


def load_blender_config() -> BlenderConfig:
    port, port_issue = _resolve_int(
        os.environ.get("BLENDER_PORT"),
        default=DEFAULT_BLENDER_PORT,
        label="BLENDER_PORT",
        minimum=1,
        maximum=65535,
    )
    timeout, timeout_issue = _resolve_timeout(
        os.environ.get("BLENDER_TIMEOUT_SECONDS"),
        default=DEFAULT_BLENDER_TIMEOUT_SECONDS,
        label="BLENDER_TIMEOUT_SECONDS",
    )
    return BlenderConfig(
        host=os.environ.get("BLENDER_HOST", DEFAULT_BLENDER_HOST),
        port=port,
        timeout_seconds=timeout,
        config_issue=port_issue or timeout_issue,
    )


def load_elegoo_config() -> ElegooConfig:
    timeout, timeout_issue = _resolve_timeout(
        os.environ.get("ELEGOO_TIMEOUT_SECONDS"),
        default=DEFAULT_ELEGOO_TIMEOUT_SECONDS,
        label="ELEGOO_TIMEOUT_SECONDS",
    )
    discovery_timeout, discovery_issue = _resolve_timeout(
        os.environ.get("ELEGOO_DISCOVERY_TIMEOUT_SECONDS"),
        default=DEFAULT_ELEGOO_DISCOVERY_TIMEOUT_SECONDS,
        label="ELEGOO_DISCOVERY_TIMEOUT_SECONDS",
    )
    chunk_bytes, chunk_issue = _resolve_int(
        os.environ.get("ELEGOO_UPLOAD_CHUNK_BYTES"),
        default=DEFAULT_ELEGOO_UPLOAD_CHUNK_BYTES,
        label="ELEGOO_UPLOAD_CHUNK_BYTES",
        minimum=1,
        maximum=DEFAULT_ELEGOO_UPLOAD_CHUNK_BYTES,
    )
    return ElegooConfig(
        host=os.environ.get("ELEGOO_PRINTER_HOST") or None,
        access_code=os.environ.get("ELEGOO_ACCESS_CODE") or None,
        use_default_access_code=_env_bool("ELEGOO_USE_DEFAULT_ACCESS_CODE"),
        serial_number=os.environ.get("ELEGOO_SERIAL_NUMBER") or None,
        mainboard_id=os.environ.get("ELEGOO_MAINBOARD_ID") or None,
        printer_type=os.environ.get("ELEGOO_PRINTER_TYPE") or None,
        timeout_seconds=timeout,
        discovery_timeout_seconds=discovery_timeout,
        upload_chunk_bytes=chunk_bytes,
        config_issue=timeout_issue or discovery_issue or chunk_issue,
    )


def load_orca_config() -> OrcaConfig:
    explicit_binary = os.environ.get("ORCASLICER_BIN")
    binary, binary_issue = _resolve_binary(explicit_binary)
    datadir = _resolve_optional_dir(os.environ.get("ORCASLICER_DATADIR"))
    timeout, timeout_issue = _resolve_timeout(
        os.environ.get("ORCASLICER_TIMEOUT_SECONDS"),
        default=DEFAULT_TIMEOUT_SECONDS,
        label="ORCASLICER_TIMEOUT_SECONDS",
    )
    return OrcaConfig(
        binary_path=binary,
        datadir=datadir,
        timeout_seconds=timeout,
        explicit_binary=explicit_binary is not None,
        binary_issue=binary_issue or timeout_issue,
    )


load_config = load_orca_config


def _candidate_env_paths(start_dir: Path) -> list[Path]:
    candidates = [
        start_dir / ".env",
        PROJECT_ROOT / ".env",
        WORKSPACE_ROOT / ".env",
    ]
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve(strict=False)
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _load_env_file(path: Path, *, override: bool) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.startswith("export "):
            stripped = stripped.removeprefix("export ").strip()
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key or (key in os.environ and not override):
            continue
        os.environ[key] = _clean_env_value(value)


def _clean_env_value(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        return cleaned[1:-1]
    return cleaned


def _resolve_binary(explicit: str | None) -> tuple[Path | None, str | None]:
    if explicit:
        path = _normalize_path(explicit)
        binary = _resolve_orca_executable(path)
        if binary is not None:
            return binary.resolve(), None
        return path, f"ORCASLICER_BIN points to a missing executable: {path}"

    for command in ORCA_PATH_COMMANDS:
        found = shutil.which(command)
        if found:
            return Path(found).resolve(), None

    for candidate in _platform_candidates():
        binary = _resolve_orca_executable(candidate)
        if binary is not None:
            return binary.resolve(), None

    return None, "No OrcaSlicer executable was found on PATH or in common install locations."


def _normalize_path(value: str) -> Path:
    cleaned = value.strip().strip('"').strip("'")
    return Path(os.path.expandvars(cleaned)).expanduser().resolve(strict=False)


def _resolve_orca_executable(path: Path) -> Path | None:
    if path.is_file():
        return path

    candidates: list[Path] = []
    system = platform.system()
    if path.is_dir():
        candidates.extend(_candidate_children(path, system))
    else:
        candidates.extend(_candidate_children(path.parent, system))

    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _candidate_children(root: Path, system: str) -> list[Path]:
    if system == "Windows":
        names = WINDOWS_ORCA_EXECUTABLES
    elif system == "Darwin":
        names = MACOS_ORCA_EXECUTABLES
    else:
        names = LINUX_ORCA_EXECUTABLES
    return [root / name for name in names]


def _platform_candidates() -> list[Path]:
    system = platform.system()
    if system == "Darwin":
        app_roots = [
            Path("/Applications/OrcaSlicer.app/Contents/MacOS"),
            Path("/Applications/Orca Slicer.app/Contents/MacOS"),
            Path.home() / "Applications/OrcaSlicer.app/Contents/MacOS",
            Path.home() / "Applications/Orca Slicer.app/Contents/MacOS",
        ]
        app_bundles = [
            Path("/Applications/OrcaSlicer.app"),
            Path("/Applications/Orca Slicer.app"),
            Path.home() / "Applications/OrcaSlicer.app",
            Path.home() / "Applications/Orca Slicer.app",
        ]
        names = ("OrcaSlicer", "orca-slicer", "orcaslicer")
        return [*app_bundles, *[root / name for root in app_roots for name in names]]

    if system == "Windows":
        roots = [
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
            str(Path(os.environ["LOCALAPPDATA"]) / "Programs")
            if os.environ.get("LOCALAPPDATA")
            else None,
        ]
        install_dirs = ("OrcaSlicer", "Orca Slicer")
        return [
            Path(root) / install_dir / name
            for root in roots
            if root
            for install_dir in install_dirs
            for name in WINDOWS_ORCA_EXECUTABLES
        ]

    linux_roots = [
        Path("/usr/bin"),
        Path("/usr/local/bin"),
        Path("/snap/bin"),
        Path("/opt/OrcaSlicer"),
        Path("/opt/OrcaSlicer/bin"),
        Path("/opt/orcaslicer"),
        Path("/opt/orcaslicer/bin"),
    ]
    return [root / name for root in linux_roots for name in LINUX_ORCA_EXECUTABLES]


def _resolve_optional_dir(value: str | None) -> Path | None:
    if not value:
        return None
    return Path(value).expanduser().resolve()


def _resolve_timeout(
    value: str | None,
    *,
    default: float,
    label: str,
) -> tuple[float, str | None]:
    if not value:
        return default, None
    try:
        timeout = float(value)
    except ValueError:
        return default, (
            f"{label} must be a positive number; got {value!r}. Using default {default}."
        )
    if timeout <= 0:
        return default, (
            f"{label} must be greater than zero; got {value!r}. Using default {default}."
        )
    return timeout, None


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_int(
    value: str | None,
    *,
    default: int,
    label: str,
    minimum: int,
    maximum: int,
) -> tuple[int, str | None]:
    if not value:
        return default, None
    try:
        parsed = int(value)
    except ValueError:
        return default, f"{label} must be an integer; got {value!r}. Using default {default}."
    if not minimum <= parsed <= maximum:
        return default, (
            f"{label} must be between {minimum} and {maximum}; got {value!r}. "
            f"Using default {default}."
        )
    return parsed, None
