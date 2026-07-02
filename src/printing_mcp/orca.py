from __future__ import annotations

import subprocess  # nosec B404 - OrcaRunner executes the configured local slicer binary.
from collections.abc import Iterable
from pathlib import Path
from platform import system

from .artifact_validation import validate_gcode_path
from .config import OrcaConfig, load_orca_config
from .files import (
    discover_files,
    read_json_file_result,
    resolve_existing_files,
    resolve_output_dir,
    resolve_parent_for_output,
)
from .types import (
    CommandResult,
    Export3mfRequest,
    ExportSettingsRequest,
    ExportStlRequest,
    GCodeValidationOptions,
    InspectModelRequest,
    JsonObject,
    SliceRequest,
)

MAX_CAPTURED_CHARS = 30_000


class OrcaRunner:
    def __init__(self, config: OrcaConfig | None = None) -> None:
        self.config = config or load_orca_config()

    def reload_config(self) -> OrcaConfig:
        self.config = load_orca_config()
        return self.config

    def help(
        self,
        *,
        include_print_options: bool = False,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        args = ["--help"]
        if include_print_options:
            args.append("--help-fff")
        return self.run(args, timeout_seconds=timeout_seconds)

    def slice_model(self, request: SliceRequest) -> CommandResult:
        model_paths = resolve_existing_files(request.model_paths, label="model_paths")
        output_dir = resolve_output_dir(request.output_dir)
        settings = resolve_existing_files(request.settings_paths, label="settings_paths")
        filaments = resolve_existing_files(request.filament_paths, label="filament_paths")

        args = self._common_args(request.datadir)
        args.extend(["--slice", str(request.plate), "--outputdir", str(output_dir)])
        args.extend(self._path_list_arg("--load-settings", settings))
        args.extend(self._path_list_arg("--load-filaments", filaments))
        if request.arrange is not None:
            args.extend(["--arrange", str(request.arrange)])
        if request.orient is not None:
            args.extend(["--orient", str(request.orient)])
        if request.scale is not None:
            args.extend(["--scale", str(request.scale)])
        args.extend(request.extra_args)
        args.extend(str(path) for path in model_paths)

        result = self.run(
            args,
            timeout_seconds=request.timeout_seconds,
            discovery_paths=[output_dir],
            result_json_path=output_dir / "result.json",
        )
        if request.validate_gcode:
            self._attach_gcode_validation(result, request.gcode_validation)
        return result

    def export_3mf(self, request: Export3mfRequest) -> CommandResult:
        model_paths = resolve_existing_files(request.model_paths, label="model_paths")
        target_path = resolve_parent_for_output(request.target_path)
        output_dir = (
            resolve_output_dir(request.output_dir) if request.output_dir else target_path.parent
        )
        settings = resolve_existing_files(request.settings_paths, label="settings_paths")
        filaments = resolve_existing_files(request.filament_paths, label="filament_paths")

        args = self._common_args(request.datadir)
        generated_target = output_dir / target_path.name
        args.extend(["--export-3mf", target_path.name, "--outputdir", str(output_dir)])
        args.extend(self._path_list_arg("--load-settings", settings))
        args.extend(self._path_list_arg("--load-filaments", filaments))
        if request.min_save:
            args.append("--min-save")
        args.extend(request.extra_args)
        args.extend(str(path) for path in model_paths)

        return self.run(
            args,
            timeout_seconds=request.timeout_seconds,
            discovery_paths=[generated_target, output_dir],
            result_json_path=output_dir / "result.json",
        )

    def export_stl(self, request: ExportStlRequest) -> CommandResult:
        model_paths = resolve_existing_files(request.model_paths, label="model_paths")
        output_dir = resolve_output_dir(request.output_dir) if request.output_dir else None
        target_dir = resolve_output_dir(request.target_dir) if request.target_dir else output_dir
        settings = resolve_existing_files(request.settings_paths, label="settings_paths")
        filaments = resolve_existing_files(request.filament_paths, label="filament_paths")

        args = self._common_args(request.datadir)
        if output_dir is not None:
            args.extend(["--outputdir", str(output_dir)])
        args.extend(self._path_list_arg("--load-settings", settings))
        args.extend(self._path_list_arg("--load-filaments", filaments))
        if request.mode == "multiple":
            if target_dir is None:
                raise ValueError("target_dir or output_dir is required when mode is 'multiple'.")
            args.extend(["--export-stls", str(target_dir)])
        else:
            args.append("--export-stl")
        args.extend(request.extra_args)
        args.extend(str(path) for path in model_paths)

        discovery_paths = [path for path in (target_dir, output_dir) if path is not None]
        return self.run(
            args,
            timeout_seconds=request.timeout_seconds,
            discovery_paths=discovery_paths,
        )

    def inspect_model(self, request: InspectModelRequest) -> CommandResult:
        model_paths = resolve_existing_files(request.model_paths, label="model_paths")
        settings = resolve_existing_files(request.settings_paths, label="settings_paths")
        filaments = resolve_existing_files(request.filament_paths, label="filament_paths")

        args = self._common_args(request.datadir)
        args.append("--info")
        args.extend(self._path_list_arg("--load-settings", settings))
        args.extend(self._path_list_arg("--load-filaments", filaments))
        args.extend(request.extra_args)
        args.extend(str(path) for path in model_paths)
        return self.run(args, timeout_seconds=request.timeout_seconds)

    def export_settings(self, request: ExportSettingsRequest) -> CommandResult:
        model_paths = resolve_existing_files(request.model_paths, label="model_paths")
        target_path = resolve_parent_for_output(request.target_path)
        output_dir = (
            resolve_output_dir(request.output_dir) if request.output_dir else target_path.parent
        )
        settings = resolve_existing_files(request.settings_paths, label="settings_paths")
        filaments = resolve_existing_files(request.filament_paths, label="filament_paths")

        args = self._common_args(request.datadir)
        args.extend(["--export-settings", str(target_path), "--outputdir", str(output_dir)])
        args.extend(self._path_list_arg("--load-settings", settings))
        args.extend(self._path_list_arg("--load-filaments", filaments))
        args.extend(request.extra_args)
        args.extend(str(path) for path in model_paths)
        return self.run(
            args,
            timeout_seconds=request.timeout_seconds,
            discovery_paths=[target_path, output_dir],
            result_json_path=output_dir / "result.json",
        )

    def run(
        self,
        args: list[str],
        *,
        timeout_seconds: float | None = None,
        discovery_paths: list[Path] | None = None,
        result_json_path: Path | None = None,
    ) -> CommandResult:
        if not self.config.available or self.config.binary_path is None:
            binary_hint = (
                f" Current ORCASLICER_BIN value: {self.config.binary_path}."
                if self.config.binary_path
                else ""
            )
            return CommandResult(
                ok=False,
                returncode=None,
                command=args,
                error_type="missing_binary",
                timeout_seconds=timeout_seconds or self.config.timeout_seconds,
                suggestions=[
                    (
                        "Install OrcaSlicer, or set ORCASLICER_BIN to the executable "
                        "or install folder."
                    ),
                    (
                        "On Windows, prefer orca-slicer-console.exe when present; "
                        "otherwise use orca-slicer.exe."
                    ),
                    (
                        "On macOS, the path is often "
                        "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer."
                    ),
                    "After changing MCP environment variables, restart the MCP server or editor.",
                ],
                message=(
                    self.config.binary_issue
                    or "OrcaSlicer binary not found. Set ORCASLICER_BIN to the executable path."
                )
                + binary_hint,
            )

        command = [str(self.config.binary_path), *args]
        timeout = timeout_seconds or self.config.timeout_seconds
        try:
            completed = subprocess.run(  # nosec B603 - command uses an explicit binary path and arg list.
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            result_json_result = (
                read_json_file_result(result_json_path) if result_json_path else None
            )
            return CommandResult(
                ok=False,
                returncode=None,
                command=command,
                error_type="timeout",
                stdout=self._truncate(self._as_text(error.stdout)),
                stderr=self._truncate(self._as_text(error.stderr)),
                timed_out=True,
                timeout_seconds=timeout,
                generated_files=discover_files(discovery_paths or []),
                result_json=result_json_result.data if result_json_result else None,
                result_json_status=result_json_result.status if result_json_result else None,
                result_json_error=result_json_result.error if result_json_result else None,
                suggestions=[
                    (
                        "Increase ORCASLICER_TIMEOUT_SECONDS or request timeout_seconds "
                        "for large models."
                    ),
                    (
                        "Try slicing the file in OrcaSlicer directly to confirm the model "
                        "and profiles are valid."
                    ),
                    "Reduce model complexity or slice a single plate with plate > 0.",
                ],
                message=(
                    f"OrcaSlicer command timed out after {timeout} seconds. "
                    "Partial output and any discovered files are included when available."
                ),
            )
        except OSError as error:
            return CommandResult(
                ok=False,
                returncode=None,
                command=command,
                error_type="spawn_error",
                timeout_seconds=timeout,
                suggestions=[
                    "Verify ORCASLICER_BIN points to an executable file.",
                    "Check OS permissions and quarantine prompts for the OrcaSlicer app.",
                ],
                message=f"Failed to start OrcaSlicer: {error}",
            )

        stdout = self._truncate(completed.stdout)
        stderr = self._truncate(completed.stderr)
        result_json_result = read_json_file_result(result_json_path) if result_json_path else None
        result_json = result_json_result.data if result_json_result else None
        ok = completed.returncode == 0

        return CommandResult(
            ok=ok,
            returncode=completed.returncode,
            command=command,
            error_type=None if ok else "orcaslicer_cli_error",
            stdout=stdout,
            stderr=stderr,
            timeout_seconds=timeout,
            generated_files=discover_files(discovery_paths or []),
            result_json=result_json,
            result_json_status=result_json_result.status if result_json_result else None,
            result_json_error=result_json_result.error if result_json_result else None,
            suggestions=[] if ok else self._failure_suggestions(stderr, result_json),
            message=None
            if ok
            else self._failure_message(completed.returncode, stdout, stderr, result_json),
        )

    def _common_args(self, request_datadir: str | None) -> list[str]:
        datadir = (
            Path(request_datadir).expanduser().resolve() if request_datadir else self.config.datadir
        )
        return ["--datadir", str(datadir)] if datadir else []

    @staticmethod
    def _attach_gcode_validation(
        result: CommandResult,
        options: GCodeValidationOptions,
    ) -> None:
        gcode_files = [
            file
            for file in result.generated_files
            if file.kind in {"gcode", "bgcode"}
            or Path(file.path).suffix.lower() in {".gcode", ".bgcode"}
        ]
        validations = [
            validate_gcode_path(file.path, options).model_dump(mode="json") for file in gcode_files
        ]
        data = dict(result.data or {})
        data["gcode_validation"] = validations
        result.data = data

        if not result.ok:
            return
        if not validations:
            result.ok = False
            result.error_type = "validation_error"
            result.message = (
                "G-code validation was requested, but OrcaSlicer produced no G-code files."
            )
            result.suggestions = [
                (
                    "Check output_dir and OrcaSlicer stdout/stderr to confirm where slice "
                    "outputs were written."
                )
            ]
            return
        failed = [validation for validation in validations if not validation.get("ok")]
        if failed:
            result.ok = False
            result.error_type = "validation_error"
            result.message = f"G-code validation failed for {len(failed)} generated file(s)."
            result.suggestions = [
                "Inspect result.data.gcode_validation for exact threshold failures.",
                "Revise model geometry or slicer settings before uploading the generated file.",
            ]

    @staticmethod
    def _path_list_arg(flag: str, paths: Iterable[Path]) -> list[str]:
        values = [str(path) for path in paths]
        return [flag, ";".join(values)] if values else []

    @staticmethod
    def _truncate(value: str) -> str:
        if len(value) <= MAX_CAPTURED_CHARS:
            return value
        return value[:MAX_CAPTURED_CHARS] + "\n...[truncated]"

    @staticmethod
    def _as_text(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

    @staticmethod
    def _failure_message(
        returncode: int,
        stdout: str,
        stderr: str,
        result_json: JsonObject | None,
    ) -> str:
        result_error = result_json.get("error_string") if result_json else None
        if isinstance(result_error, str) and result_error.strip():
            return f"OrcaSlicer failed with exit code {returncode}: {result_error.strip()}"

        for stream_name, text in (("stderr", stderr), ("stdout", stdout)):
            first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
            if first_line:
                return (
                    f"OrcaSlicer failed with exit code {returncode}. "
                    f"First {stream_name} line: {first_line}"
                )

        return f"OrcaSlicer failed with exit code {returncode} and produced no diagnostic output."

    @staticmethod
    def _failure_suggestions(
        stderr: str,
        result_json: JsonObject | None,
    ) -> list[str]:
        suggestions = [
            (
                "Open the same model in OrcaSlicer to confirm the file and selected "
                "profiles are valid."
            ),
            "Check model_paths, settings_paths, filament_paths, datadir, and output_dir for typos.",
            (
                "Inspect stderr, stdout, and result_json in this response for the original "
                "OrcaSlicer diagnostic."
            ),
        ]
        combined = f"{stderr}\n{result_json or {}}".lower()
        if "no such file" in combined or "not found" in combined:
            suggestions.insert(0, "Use absolute paths for all input files and profile JSON files.")
        if "invalid option" in combined:
            suggestions.insert(
                0, "Remove unsupported extra_args or check them with orcaslicer_help."
            )
        if system() == "Windows" and not stderr.strip():
            suggestions.insert(
                0,
                (
                    "Some OrcaSlicer Windows builds are GUI executables with unreliable "
                    "stdout/stderr; try upgrading OrcaSlicer or point ORCASLICER_BIN to "
                    "orca-slicer-console.exe if your install includes it."
                ),
            )
        return suggestions
