from __future__ import annotations

import json
import logging
import socket
from pathlib import Path
from typing import cast

from .config import BlenderConfig, load_blender_config
from .types import ErrorType, JsonObject, ToolResult

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
LOGGER = logging.getLogger(__name__)


class BlenderConnection:
    """Small JSON-over-TCP client for the lean Blender addon."""

    def __init__(self, config: BlenderConfig | None = None) -> None:
        self.config = config or load_blender_config()

    def send_command(
        self,
        command_type: str,
        params: JsonObject | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> ToolResult:
        timeout = timeout_seconds or self.config.timeout_seconds
        command: JsonObject = {"type": command_type, "params": params or {}}
        try:
            with socket.create_connection(
                (self.config.host, self.config.port),
                timeout=timeout,
            ) as sock:
                sock.settimeout(timeout)
                sock.sendall(json.dumps(command).encode("utf-8"))
                response = self._receive_json(sock)
        except TimeoutError:
            return self._error(
                "timeout",
                f"Blender command {command_type!r} timed out after {timeout} seconds.",
                [
                    "Try a smaller Blender script or increase BLENDER_TIMEOUT_SECONDS.",
                    "Check Blender for a blocking modal dialog or long-running operation.",
                ],
            )
        except OSError as error:
            return self._error(
                "connection_error",
                f"Could not connect to Blender at {self.config.host}:{self.config.port}: {error}",
                [
                    "Launch Blender and start the lean Printing MCP addon.",
                    "Check BLENDER_HOST and BLENDER_PORT match the addon panel.",
                    "If another MCP uses port 9876, change one server's port.",
                ],
            )
        except json.JSONDecodeError as error:
            return self._error(
                "backend_error",
                f"Blender returned invalid JSON for {command_type!r}: {error}",
                ["Restart the Blender addon and retry the command."],
            )
        except ValueError as error:
            return self._error("backend_error", str(error), ["Restart the Blender addon."])

        if response.get("status") == "success":
            result = response.get("result")
            return ToolResult(
                ok=True,
                backend="blender",
                data=cast(JsonObject, result) if isinstance(result, dict) else {"result": result},
            )

        message = response.get("message")
        return self._error(
            "backend_error",
            str(message or f"Blender command {command_type!r} failed."),
            [
                "Run blender_get_scene_info to confirm Blender scene state.",
                "If this came from blender_execute_code, inspect the Python traceback in Blender.",
            ],
        )

    def _receive_json(self, sock: socket.socket) -> JsonObject:
        chunks: list[bytes] = []
        received_bytes = 0
        last_decode_error: json.JSONDecodeError | None = None
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                if not chunks:
                    raise ValueError("Blender closed the connection before sending a response.")
                break

            chunks.append(chunk)
            received_bytes += len(chunk)
            if received_bytes > MAX_RESPONSE_BYTES:
                raise ValueError("Blender response exceeded the MCP size limit.")

            decoded = b"".join(chunks).decode("utf-8")
            data, decode_error = _try_parse_json(decoded)
            if decode_error is None:
                if not isinstance(data, dict):
                    raise ValueError("Blender returned a non-object JSON response.")
                return cast(JsonObject, data)
            last_decode_error = decode_error

        decoded = b"".join(chunks).decode("utf-8")
        try:
            data = json.loads(decoded)
        except json.JSONDecodeError as error:
            if last_decode_error is not None:
                raise last_decode_error from error
            raise
        if not isinstance(data, dict):
            raise ValueError("Blender returned a non-object JSON response.")
        return cast(JsonObject, data)

    @staticmethod
    def _error(error_type: ErrorType, message: str, suggestions: list[str]) -> ToolResult:
        return ToolResult(
            ok=False,
            backend="blender",
            error_type=error_type,
            message=message,
            suggestions=suggestions,
        )


def _try_parse_json(value: str) -> tuple[object | None, json.JSONDecodeError | None]:
    try:
        return json.loads(value), None
    except json.JSONDecodeError as error:
        LOGGER.debug("Waiting for more Blender JSON response bytes: %s", error)
        return None, error


class BlenderRunner:
    """Lean Blender backend matching the tools this printing repo actually uses."""

    def __init__(self, config: BlenderConfig | None = None) -> None:
        self.config = config or load_blender_config()
        self.connection = BlenderConnection(self.config)

    def reload_config(self) -> BlenderConfig:
        self.config = load_blender_config()
        self.connection = BlenderConnection(self.config)
        return self.config

    def status(self) -> ToolResult:
        return self.connection.send_command("get_scene_info", timeout_seconds=5.0)

    def get_scene_info(self) -> ToolResult:
        return self.connection.send_command("get_scene_info")

    def get_capabilities(self) -> ToolResult:
        return self.connection.send_command("get_capabilities")

    def get_object_info(self, object_name: str) -> ToolResult:
        return self.connection.send_command("get_object_info", {"name": object_name})

    def validate_object(self, object_name: str, *, evaluated: bool) -> ToolResult:
        return self.connection.send_command(
            "validate_object",
            {
                "object_name": object_name,
                "evaluated": evaluated,
            },
        )

    def review_printability(
        self,
        object_name: str,
        *,
        evaluated: bool,
        bed_size_mm: tuple[float, float, float],
        overhang_degrees: float,
        min_feature_mm: float,
        require_single_component: bool,
    ) -> ToolResult:
        return self.connection.send_command(
            "review_printability",
            {
                "object_name": object_name,
                "evaluated": evaluated,
                "bed_size_mm": list(bed_size_mm),
                "overhang_degrees": overhang_degrees,
                "min_feature_mm": min_feature_mm,
                "require_single_component": require_single_component,
            },
        )

    def review_visuals(
        self,
        object_name: str,
        *,
        output_dir: Path,
        max_size: int,
        include_top: bool,
        include_front: bool,
        include_right: bool,
        include_isometric: bool,
    ) -> ToolResult:
        return self.connection.send_command(
            "review_visuals",
            {
                "object_name": object_name,
                "output_dir": str(output_dir),
                "max_size": max_size,
                "include_top": include_top,
                "include_front": include_front,
                "include_right": include_right,
                "include_isometric": include_isometric,
            },
        )

    def export_stl(
        self,
        object_name: str,
        target_path: Path,
        *,
        global_scale: float,
        validate: bool,
        allow_non_manifold: bool,
    ) -> ToolResult:
        return self.connection.send_command(
            "export_stl",
            {
                "object_name": object_name,
                "target_path": str(target_path),
                "global_scale": global_scale,
                "validate": validate,
                "allow_non_manifold": allow_non_manifold,
            },
        )

    def bootstrap_model(self, model_name: str, *, version: int, output_root: Path) -> ToolResult:
        return self.connection.send_command(
            "bootstrap_model",
            {
                "model_name": model_name,
                "version": version,
                "output_root": str(output_root),
            },
        )

    def execute_code(self, code: str, *, timeout_seconds: float | None = None) -> ToolResult:
        return self.connection.send_command(
            "execute_code",
            {"code": code},
            timeout_seconds=timeout_seconds,
        )

    def capture_viewport_screenshot(self, filepath: Path, *, max_size: int) -> ToolResult:
        return self.connection.send_command(
            "get_viewport_screenshot",
            {
                "filepath": str(filepath),
                "format": "png",
                "max_size": max_size,
            },
        )
