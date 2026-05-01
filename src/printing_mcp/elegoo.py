from __future__ import annotations

import hashlib
import json
import random
import socket
import threading
import time
import uuid
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import paho.mqtt.client as mqtt
from websockets.sync.client import connect as websocket_connect

from .config import ElegooConfig, load_elegoo_config
from .files import resolve_existing_file
from .types import (
    ElegooPrinterInfo,
    ElegooPrinterRequest,
    ElegooStartPrintRequest,
    ElegooUploadRequest,
    ErrorType,
    JsonObject,
    JsonValue,
    PrinterType,
    ToolResult,
)

CC2_DISCOVERY_PORT = 52700
CC_DISCOVERY_PORT = 3000
CC2_MQTT_PORT = 1883
CC_WEBSOCKET_PORT = 3030
# Elegoo's LAN adapters document these firmware command IDs for filament routines.
ELEGOO_LOAD_FILAMENT_COMMAND = 1024
ELEGOO_UNLOAD_FILAMENT_COMMAND = 1025
DEFAULT_ACCESS_TOKEN = "123456"
MQTT_USERNAME = "elegoo"
ELEGOO_LINK_USER_AGENT = "ElegooLink/1.0"
MAX_DATAGRAM_BYTES = 65535
CC2_DISCOVERY_PAYLOAD = b'{"id":0,"method":7000}'
CC_DISCOVERY_PAYLOAD = b"M99999"


class ElegooLanRunner:
    """LAN-only Elegoo machine client. Cloud/WAN endpoints are intentionally excluded."""

    def __init__(self, config: ElegooConfig | None = None) -> None:
        self.config = config or load_elegoo_config()

    def reload_config(self) -> ElegooConfig:
        self.config = load_elegoo_config()
        return self.config

    def discover(
        self,
        timeout_seconds: float | None = None,
        *,
        include_cc2: bool = True,
        include_cc: bool = True,
    ) -> ToolResult:
        timeout = timeout_seconds or self.config.discovery_timeout_seconds
        started_at = time.monotonic()
        printers: dict[str, ElegooPrinterInfo] = {}

        strategies = []
        if include_cc2:
            strategies.append((CC2_DISCOVERY_PORT, CC2_DISCOVERY_PAYLOAD))
        if include_cc:
            strategies.append((CC_DISCOVERY_PORT, CC_DISCOVERY_PAYLOAD))

        for port, payload in strategies:
            remaining = max(0.1, timeout - (time.monotonic() - started_at))
            for printer in self._discover_port(port, payload, remaining):
                printers[printer.printer_id] = printer

        return ToolResult(
            ok=True,
            backend="elegoo",
            data={
                "printers": [
                    cast(JsonObject, printer.model_dump(mode="json"))
                    for printer in printers.values()
                ]
            },
            message=f"Discovered {len(printers)} LAN Elegoo printer(s).",
        )

    def status(self, request: ElegooPrinterRequest) -> ToolResult:
        target = self._target(request)
        if isinstance(target, ToolResult):
            return target
        if target.printer_type == "cc2":
            return self._cc2_command(
                target,
                method=1002,
                params={},
                access_code=self._access_code(request),
                timeout_seconds=request.timeout_seconds,
            )
        if target.printer_type == "cc":
            return self._cc_command(target, command=0, data={})
        return self._unsupported_printer_type(target.printer_type)

    def upload(self, request: ElegooUploadRequest) -> ToolResult:
        target = self._target(request)
        if isinstance(target, ToolResult):
            return target

        file_path = resolve_existing_file(request.file_path, label="file_path")
        file_name = _remote_file_name(request.file_name, request.remote_path, file_path)
        chunk_bytes = request.chunk_bytes or self.config.upload_chunk_bytes
        if target.printer_type == "cc2":
            access_code = self._access_code(request)
            busy_result = self._cc2_is_busy_for_upload(
                target,
                access_code=access_code,
                timeout_seconds=request.timeout_seconds,
            )
            if isinstance(busy_result, ToolResult):
                return busy_result
            return self._cc2_upload(
                target,
                file_path,
                file_name,
                chunk_bytes,
                access_code=access_code,
                timeout_seconds=request.timeout_seconds,
            )
        if target.printer_type == "cc":
            return self._cc_upload(target, file_path, file_name, chunk_bytes)
        return self._unsupported_printer_type(target.printer_type)

    def start_print(self, request: ElegooStartPrintRequest) -> ToolResult:
        target = self._target(request)
        if isinstance(target, ToolResult):
            return target
        if target.printer_type == "cc2":
            return self._cc2_command(
                target,
                method=1020,
                params=_cc2_start_params(request),
                access_code=self._access_code(request),
                timeout_seconds=request.timeout_seconds,
            )
        if target.printer_type == "cc":
            return self._cc_command(target, command=128, data=_cc_start_data(request))
        return self._unsupported_printer_type(target.printer_type)

    def upload_and_start_print(
        self,
        upload_request: ElegooUploadRequest,
        start_request: ElegooStartPrintRequest,
    ) -> ToolResult:
        upload_result = self.upload(upload_request)
        if not upload_result.ok:
            return upload_result
        start_result = self.start_print(start_request)
        start_data = start_result.data or {}
        start_data["upload"] = upload_result.model_dump(mode="json")
        start_result.data = start_data
        return start_result

    def pause_print(self, request: ElegooPrinterRequest) -> ToolResult:
        target = self._target(request)
        if isinstance(target, ToolResult):
            return target
        if target.printer_type == "cc2":
            return self._cc2_command(
                target,
                method=1021,
                params={},
                access_code=self._access_code(request),
                timeout_seconds=request.timeout_seconds,
            )
        if target.printer_type == "cc":
            return self._cc_command(target, command=129, data={})
        return self._unsupported_printer_type(target.printer_type)

    def resume_print(self, request: ElegooPrinterRequest) -> ToolResult:
        target = self._target(request)
        if isinstance(target, ToolResult):
            return target
        if target.printer_type == "cc2":
            return self._cc2_command(
                target,
                method=1023,
                params={},
                access_code=self._access_code(request),
                timeout_seconds=request.timeout_seconds,
                fallback_sdcp_command=131,
            )
        if target.printer_type == "cc":
            return self._cc_command(target, command=131, data={})
        return self._unsupported_printer_type(target.printer_type)

    def cancel_print(self, request: ElegooPrinterRequest) -> ToolResult:
        target = self._target(request)
        if isinstance(target, ToolResult):
            return target
        if target.printer_type == "cc2":
            return self._cc2_command(
                target,
                method=1022,
                params={},
                access_code=self._access_code(request),
                timeout_seconds=request.timeout_seconds,
                fallback_sdcp_command=130,
            )
        if target.printer_type == "cc":
            return self._cc_command(target, command=130, data={})
        return self._unsupported_printer_type(target.printer_type)

    def stop_print(self, request: ElegooPrinterRequest) -> ToolResult:
        target = self._target(request)
        if isinstance(target, ToolResult):
            return target
        if target.printer_type == "cc2":
            return self._cc2_command(
                target,
                method=1022,
                params={},
                access_code=self._access_code(request),
                timeout_seconds=request.timeout_seconds,
            )
        if target.printer_type == "cc":
            return self._cc_command(target, command=130, data={})
        return self._unsupported_printer_type(target.printer_type)

    def load_filament(self, request: ElegooPrinterRequest) -> ToolResult:
        return self._filament_command(request, command=ELEGOO_LOAD_FILAMENT_COMMAND, action="load")

    def unload_filament(self, request: ElegooPrinterRequest) -> ToolResult:
        return self._filament_command(
            request, command=ELEGOO_UNLOAD_FILAMENT_COMMAND, action="unload"
        )

    def _filament_command(
        self,
        request: ElegooPrinterRequest,
        *,
        command: int,
        action: str,
    ) -> ToolResult:
        target = self._target(request)
        if isinstance(target, ToolResult):
            return target

        if target.printer_type == "cc2":
            result = self._cc2_command(
                target,
                method=command,
                params={},
                access_code=self._access_code(request),
                timeout_seconds=request.timeout_seconds,
            )
            return _annotate_command_result(
                result,
                action=action,
                command={"method": command, "params": {}},
                message=f"Sent CC2 filament {action} command to {target.host}.",
            )

        if target.printer_type == "cc":
            result = self._cc_command(target, command=command, data={})
            return _annotate_command_result(
                result,
                action=action,
                command={"Cmd": command, "Data": {}},
                message=f"Sent Centauri filament {action} command to {target.host}.",
            )

        return self._unsupported_printer_type(target.printer_type)

    def _discover_port(
        self,
        port: int,
        payload: bytes,
        timeout_seconds: float,
    ) -> list[ElegooPrinterInfo]:
        discovered: list[ElegooPrinterInfo] = []
        deadline = time.monotonic() + timeout_seconds
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(0.5)
            sock.bind(("", 0))
            sock.sendto(payload, ("255.255.255.255", port))
            while time.monotonic() < deadline:
                try:
                    data, address = sock.recvfrom(MAX_DATAGRAM_BYTES)
                except TimeoutError:
                    continue
                except OSError:
                    break
                printer = self._parse_discovery_response(port, address[0], data)
                if printer is not None and printer.network_mode == "lan":
                    discovered.append(printer)
        return discovered

    def _parse_discovery_response(
        self,
        port: int,
        host: str,
        data: bytes,
    ) -> ElegooPrinterInfo | None:
        try:
            payload: object = json.loads(data.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return None

        if port == CC2_DISCOVERY_PORT:
            if not isinstance(payload, dict):
                return None
            payload_object = cast(JsonObject, payload)
            result = payload_object.get("result")
            if not isinstance(result, dict) or result.get("lan_status") != 1:
                return None
            serial = _string_or_none(result.get("sn"))
            if not serial:
                return None
            requires_access_code = result.get("token_status") == 1
            return ElegooPrinterInfo(
                printer_id=f"elegoo_lan_{serial}",
                host=host,
                printer_type="cc2",
                name=_string_or_none(result.get("host_name")),
                model=_string_or_none(result.get("machine_model")),
                serial_number=serial,
                mainboard_id=serial,
                auth_mode="accessCode" if requires_access_code else None,
                requires_access_code=requires_access_code,
            )

        if not isinstance(payload, dict):
            return None
        payload_object = cast(JsonObject, payload)
        data_payload = payload_object.get("Data")
        if not isinstance(data_payload, dict):
            return None
        mainboard_id = _string_or_none(data_payload.get("MainboardID"))
        if not mainboard_id:
            return None
        firmware = _string_or_none(data_payload.get("FirmwareVersion"))
        if firmware and firmware.startswith("V"):
            firmware = firmware[1:]
        return ElegooPrinterInfo(
            printer_id=f"elegoo_lan_{mainboard_id}",
            host=host,
            printer_type="cc",
            name=_string_or_none(data_payload.get("Name")),
            model=_string_or_none(data_payload.get("MachineName")),
            mainboard_id=mainboard_id,
            firmware_version=firmware,
        )

    def _target(self, request: ElegooPrinterRequest) -> ElegooPrinterInfo | ToolResult:
        host = request.host or self.config.host
        printer_type = request.printer_type or self.config.printer_type
        if not host:
            return _error(
                "invalid_request",
                "Elegoo LAN host is required. Pass host or set ELEGOO_PRINTER_HOST.",
                [
                    "Run elegoo_discover_lan_printers to find LAN printers.",
                    "Use the discovered host and printer_type for machine commands.",
                ],
            )
        if printer_type not in {"cc2", "cc"}:
            return _error(
                "invalid_request",
                "Elegoo printer_type is required and must be 'cc2' or 'cc'.",
                ["Pass printer_type from discovery or set ELEGOO_PRINTER_TYPE."],
            )
        typed_printer_type = cast(PrinterType, printer_type)
        return ElegooPrinterInfo(
            printer_id=f"elegoo_lan_{request.serial_number or request.mainboard_id or host}",
            host=host,
            printer_type=typed_printer_type,
            serial_number=request.serial_number or self.config.serial_number,
            mainboard_id=request.mainboard_id or self.config.mainboard_id,
            auth_mode="accessCode" if request.access_code or self.config.access_code else None,
        )

    def _access_code(self, request: ElegooPrinterRequest) -> str:
        return request.access_code or self.config.access_code or DEFAULT_ACCESS_TOKEN

    def _cc2_command(
        self,
        target: ElegooPrinterInfo,
        *,
        method: int,
        params: JsonObject,
        access_code: str,
        timeout_seconds: float | None = None,
        fallback_sdcp_command: int | None = None,
    ) -> ToolResult:
        if target.serial_number is None:
            serial_result = self._cc2_fetch_serial(target.host, access_code)
            if not serial_result.ok:
                return serial_result
            if serial_result.data is None:
                return _error("backend_error", "CC2 serial lookup returned no data.", [])
            target.serial_number = str(serial_result.data["serial_number"])

        try:
            with _Cc2MqttSession(
                host=target.host,
                serial_number=target.serial_number,
                access_code=access_code,
                timeout_seconds=timeout_seconds or self.config.timeout_seconds,
            ) as session:
                response = session.request(
                    method,
                    params,
                    fallback_sdcp_command=fallback_sdcp_command,
                )
        except TimeoutError as error:
            return _error("timeout", str(error), ["Confirm the printer is on LAN and not asleep."])
        except OSError as error:
            return _error(
                "connection_error",
                f"Could not connect to CC2 MQTT at {target.host}:1883: {error}",
                ["Check the printer IP, LAN mode, and access code."],
            )

        return ToolResult(ok=True, backend="elegoo", data={"response": response})

    def _cc2_fetch_serial(self, host: str, access_code: str) -> ToolResult:
        url = f"http://{host}/system/info?X-Token={quote(access_code)}"
        response = _http_json("GET", url, headers={"X-Token": access_code}, timeout=10.0)
        if not response.ok:
            return response
        system_info = (response.data or {}).get("system_info")
        if not isinstance(system_info, dict) or not system_info.get("sn"):
            return _error(
                "backend_error",
                "CC2 /system/info did not include system_info.sn.",
                ["Pass serial_number explicitly if the printer omits it from HTTP system info."],
            )
        return ToolResult(
            ok=True,
            backend="elegoo",
            data={
                "serial_number": str(system_info["sn"]),
                "system_info": cast(JsonObject, system_info),
            },
        )

    def _cc_command(
        self,
        target: ElegooPrinterInfo,
        *,
        command: int,
        data: JsonObject,
    ) -> ToolResult:
        mainboard_id = target.mainboard_id or self.config.mainboard_id
        if not mainboard_id:
            return _error(
                "invalid_request",
                "mainboard_id is required for Centauri/Centauri Carbon WebSocket commands.",
                ["Use elegoo_discover_lan_printers and pass the discovered mainboard_id."],
            )

        request_id = str(random.randint(1000, 999999))
        payload: JsonObject = {
            "Id": mainboard_id,
            "Topic": "",
            "Data": {
                "RequestID": request_id,
                "MainboardID": mainboard_id,
                "TimeStamp": int(time.time()),
                "Cmd": command,
                "From": 1,
                "Data": data,
            },
        }
        url = f"ws://{target.host}:{CC_WEBSOCKET_PORT}/websocket"
        try:
            with websocket_connect(url, open_timeout=self.config.timeout_seconds) as websocket:
                websocket.send(json.dumps(payload))
                raw_response = websocket.recv(timeout=self.config.timeout_seconds)
        except TimeoutError as error:
            return _error("timeout", f"Centauri WebSocket command timed out: {error}", [])
        except OSError as error:
            return _error(
                "connection_error",
                f"Could not connect to Centauri WebSocket at {url}: {error}",
                ["Check the printer IP and LAN mode."],
            )

        return ToolResult(ok=True, backend="elegoo", data={"response": _parse_json(raw_response)})

    def _cc2_upload(
        self,
        target: ElegooPrinterInfo,
        file_path: Path,
        file_name: str,
        chunk_bytes: int,
        access_code: str,
        timeout_seconds: float | None = None,
    ) -> ToolResult:
        token = access_code or DEFAULT_ACCESS_TOKEN
        file_md5 = _file_md5(file_path)
        file_size = file_path.stat().st_size
        timeout = timeout_seconds or self.config.timeout_seconds
        connection = HTTPConnection(target.host, timeout=timeout)
        with file_path.open("rb") as file:
            offset = 0
            try:
                while chunk := file.read(chunk_bytes):
                    result = _cc2_upload_chunk(
                        connection=connection,
                        host=target.host,
                        chunk=chunk,
                        offset=offset,
                        file_size=file_size,
                        file_name=file_name,
                        file_md5=file_md5,
                        token=token,
                    )
                    if not result.ok:
                        return result
                    offset += len(chunk)
                    time.sleep(0.001)
            finally:
                connection.close()

        return ToolResult(
            ok=True,
            backend="elegoo",
            data={"file_name": file_name, "size_bytes": file_size, "md5": file_md5},
            message=f"Uploaded {file_name} to {target.host}.",
        )

    def _cc2_is_busy_for_upload(
        self,
        target: ElegooPrinterInfo,
        *,
        access_code: str,
        timeout_seconds: float | None = None,
    ) -> bool | ToolResult:
        status_result = self._cc2_command(
            target,
            method=1002,
            params={},
            access_code=access_code,
            timeout_seconds=timeout_seconds,
        )
        if not status_result.ok:
            return status_result
        response = status_result.data.get("response") if status_result.data else None
        if not isinstance(response, dict):
            return _error(
                "backend_error",
                "CC2 status response was not an object before upload.",
                ["Retry the upload after confirming the printer is reachable in LAN mode."],
            )
        result = response.get("result")
        if not isinstance(result, dict):
            return _error(
                "backend_error",
                "CC2 status response did not include result before upload.",
                ["Retry the upload after confirming the printer is reachable in LAN mode."],
            )
        machine_status = result.get("machine_status")
        if not isinstance(machine_status, dict):
            return False
        status = machine_status.get("status")
        sub_status = machine_status.get("sub_status")
        print_status = result.get("print_status")
        has_active_file = isinstance(print_status, dict) and bool(
            print_status.get("filename") or print_status.get("uuid")
        )
        if status is None or (status == 1 and not has_active_file):
            return False
        return _error(
            "upload_error",
            f"CC2 printer is busy before upload: status={status}, sub_status={sub_status}.",
            ["Wait for the printer to return to idle before uploading."],
        )

    def _cc_upload(
        self,
        target: ElegooPrinterInfo,
        file_path: Path,
        file_name: str,
        chunk_bytes: int,
    ) -> ToolResult:
        file_md5 = _file_md5(file_path)
        file_size = file_path.stat().st_size
        upload_uuid = str(uuid.uuid4())
        with file_path.open("rb") as file:
            offset = 0
            while chunk := file.read(chunk_bytes):
                body, content_type = _multipart_body(
                    fields={
                        "Check": "1",
                        "S-File-MD5": file_md5,
                        "Offset": str(offset),
                        "Uuid": upload_uuid,
                        "TotalSize": str(file_size),
                    },
                    file_field="File",
                    file_name=file_name,
                    file_bytes=chunk,
                )
                result = _http_json(
                    "POST",
                    f"http://{target.host}/uploadFile/upload",
                    data=body,
                    headers={"Content-Type": content_type},
                    timeout=self.config.timeout_seconds,
                )
                if not result.ok:
                    return result
                if (result.data or {}).get("code") not in ("000000", None):
                    return _error(
                        "upload_error",
                        f"Centauri rejected upload chunk at byte {offset}: {result.data}",
                        ["Confirm the printer is idle and reachable over LAN."],
                    )
                offset += len(chunk)

        return ToolResult(
            ok=True,
            backend="elegoo",
            data={"file_name": file_name, "size_bytes": file_size, "md5": file_md5},
            message=f"Uploaded {file_name} to {target.host}.",
        )

    @staticmethod
    def _unsupported_printer_type(printer_type: str) -> ToolResult:
        return _error(
            "invalid_request",
            f"Printer type {printer_type!r} is not implemented for Elegoo LAN tools.",
            ["Use printer_type 'cc2' or 'cc'. Cloud/WAN/Moonraker mode is intentionally excluded."],
        )


class _Cc2MqttSession:
    def __init__(
        self,
        *,
        host: str,
        serial_number: str,
        access_code: str,
        timeout_seconds: float,
    ) -> None:
        self.host = host
        self.serial_number = serial_number
        self.timeout_seconds = timeout_seconds
        self.client_id = f"1_PC_{random.randint(1000, 9999)}"
        self.request_id = f"{self.client_id}_req"
        self.responses: dict[int, JsonObject] = {}
        self.last_status: JsonObject | None = None
        self.registration_error: str | None = None
        self.connected = threading.Event()
        self.registered = threading.Event()
        self.response_events: dict[int, threading.Event] = {}
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.client_id,
        )
        self.client.username_pw_set(MQTT_USERNAME, access_code or DEFAULT_ACCESS_TOKEN)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def __enter__(self) -> _Cc2MqttSession:
        self.client.connect(self.host, CC2_MQTT_PORT, keepalive=60)
        self.client.loop_start()
        if not self.connected.wait(self.timeout_seconds):
            raise TimeoutError("Timed out connecting to CC2 MQTT broker.")
        self._register()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def request(
        self,
        method: int,
        params: JsonObject,
        *,
        fallback_sdcp_command: int | None = None,
    ) -> JsonObject:
        request_id = random.randint(1000, 999999)
        event = threading.Event()
        self.response_events[request_id] = event
        payload: JsonObject = {"id": request_id, "method": method, "params": params}
        self.client.publish(self.command_topic, json.dumps(payload), qos=1).wait_for_publish()
        if event.wait(self.timeout_seconds):
            return self.responses.pop(request_id)
        if fallback_sdcp_command is not None:
            return self.sdcp_request(fallback_sdcp_command, {})
        if method == 1002 and self.last_status is not None:
            return self.last_status
        raise TimeoutError(f"Timed out waiting for CC2 response to method {method}.")

    def sdcp_request(self, command: int, data: JsonObject) -> JsonObject:
        request_id = uuid.uuid4().hex
        payload: JsonObject = {
            "Id": "",
            "Data": {
                "Cmd": command,
                "Data": data,
                "RequestID": request_id,
                "MainboardID": "",
                "TimeStamp": int(time.time() * 1000),
                "From": 1,
            },
        }
        self.client.publish(self.command_topic, json.dumps(payload), qos=1).wait_for_publish()
        return {"id": request_id, "method": "sdcp", "result": {"command": command, "sent": True}}

    def _register(self) -> None:
        self.client.publish(
            self.register_topic,
            json.dumps({"client_id": self.client_id, "request_id": self.request_id}),
            qos=1,
        ).wait_for_publish()
        if not self.registered.wait(self.timeout_seconds):
            raise TimeoutError("Timed out registering CC2 MQTT client.")
        if self.registration_error and self.registration_error != "ok":
            raise OSError(f"CC2 registration failed: {self.registration_error}")

    @property
    def response_topic(self) -> str:
        return f"elegoo/{self.serial_number}/{self.client_id}/api_response"

    @property
    def status_topic(self) -> str:
        return f"elegoo/{self.serial_number}/api_status"

    @property
    def register_response_topic(self) -> str:
        return f"elegoo/{self.serial_number}/{self.request_id}/register_response"

    @property
    def command_topic(self) -> str:
        return f"elegoo/{self.serial_number}/{self.client_id}/api_request"

    @property
    def register_topic(self) -> str:
        return f"elegoo/{self.serial_number}/api_register"

    def _on_connect(
        self,
        _client: mqtt.Client,
        _userdata: object,
        _flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        if reason_code.is_failure:
            return
        self.client.subscribe(self.response_topic, qos=1)
        self.client.subscribe(self.status_topic, qos=1)
        self.client.subscribe(self.register_response_topic, qos=1)
        self.connected.set()

    def _on_message(
        self,
        _client: mqtt.Client,
        _userdata: object,
        message: mqtt.MQTTMessage,
    ) -> None:
        payload = _parse_json(message.payload.decode("utf-8", errors="replace"))
        if not isinstance(payload, dict):
            return
        payload_object = cast(JsonObject, payload)
        if message.topic == self.register_response_topic:
            self.registration_error = _string_or_none(payload_object.get("error"))
            self.registered.set()
            return
        if message.topic == self.status_topic:
            self.last_status = payload_object
            return
        response_id = payload_object.get("id")
        if isinstance(response_id, int):
            self.responses[response_id] = payload_object
            event = self.response_events.get(response_id)
            if event is not None:
                event.set()


def _cc2_start_params(request: ElegooStartPrintRequest) -> JsonObject:
    storage = {"local": "local", "udisk": "u-disk", "sdcard": "sd-card"}[request.storage]
    return {
        "storage_media": storage,
        "filename": request.file_name,
        "config": {
            "delay_video": request.time_lapse,
            "printer_check": request.heated_bed_leveling,
            "print_layout": "A" if request.bed_type == 0 else "B",
            "bedlevel_force": request.bed_level_force,
            "slot_map": [
                cast(JsonObject, slot.model_dump(mode="json")) for slot in request.slot_map
            ],
        },
    }


def _remote_file_name(file_name: str | None, remote_path: str | None, file_path: Path) -> str:
    if remote_path:
        normalized_remote_path = remote_path.replace("\\", "/").strip()
        if normalized_remote_path:
            return normalized_remote_path
    if not file_name:
        return file_path.name
    normalized = file_name.replace("\\", "/").strip()
    if not normalized:
        return file_path.name
    if "/" not in normalized:
        return normalized
    return Path(normalized).name


def _cc_start_data(request: ElegooStartPrintRequest) -> JsonObject:
    return {
        "Filename": request.file_name,
        "StartLayer": 0,
        "Calibration_switch": 1 if request.heated_bed_leveling else 0,
        "PrintPlatformType": request.bed_type,
        "Tlp_Switch": 1 if request.time_lapse else 0,
        "slot_map": [cast(JsonObject, slot.model_dump(mode="json")) for slot in request.slot_map],
    }


def _cc2_upload_chunk(
    *,
    connection: HTTPConnection,
    host: str,
    chunk: bytes,
    offset: int,
    file_size: int,
    file_name: str,
    file_md5: str,
    token: str,
) -> ToolResult:
    end = offset + len(chunk) - 1
    headers = {
        "User-Agent": ELEGOO_LINK_USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(chunk)),
        "Content-Range": f"bytes {offset}-{end}/{file_size}",
        "X-File-Name": file_name,
        "X-File-MD5": file_md5,
        "X-Token": token,
    }
    for attempt in range(4):
        try:
            connection.request("PUT", "/upload", body=chunk, headers=headers)
            response = connection.getresponse()
            body = response.read()
        except (HTTPException, OSError) as error:
            connection.close()
            if attempt < 3:
                time.sleep(min(2.0, 0.25 * (2**attempt)))
                continue
            return _error(
                "connection_error",
                f"Could not upload to CC2 printer at http://{host}/upload: {error}",
                ["Check the printer IP, LAN mode, and local network connectivity."],
            )

        if response.status == 429 and attempt < 3:
            connection.close()
            time.sleep(min(4.0, 0.5 * (2**attempt)))
            continue
        if response.status < 200 or response.status >= 300:
            body_excerpt = _decode_body_excerpt(body)
            return _error(
                "backend_error",
                (
                    f"HTTP {response.status} from LAN printer for PUT http://{host}/upload."
                    f"{body_excerpt}"
                ),
                _http_suggestions(response.status)
                + [
                    (
                        f"Upload chunk offset={offset}, size={len(chunk)}, total={file_size}, "
                        f"remote_file_name={file_name!r}."
                    )
                ],
            )
        try:
            payload: object = json.loads(body.decode("utf-8")) if body else {}
        except json.JSONDecodeError:
            return _error(
                "backend_error",
                f"LAN printer returned invalid JSON from http://{host}/upload.",
                [],
            )
        if not isinstance(payload, dict):
            return _error(
                "backend_error",
                f"LAN printer returned non-object JSON from http://{host}/upload.",
                [],
            )
        if payload.get("error_code") == 1000:
            return _error(
                "upload_error",
                "LAN printer rejected the access token.",
                ["Set ELEGOO_ACCESS_CODE or pass access_code for printers that require LAN auth."],
            )
        if payload.get("error_code") not in (0, None):
            return _error(
                "upload_error",
                f"CC2 rejected upload chunk at byte {offset}: {payload}",
                ["Check ELEGOO_ACCESS_CODE and confirm the printer is not busy."],
            )
        return ToolResult(ok=True, backend="elegoo", data=cast(JsonObject, payload))

    return _error(
        "timeout",
        f"CC2 upload to http://{host}/upload was repeatedly rate limited.",
        ["Wait for the printer to become idle, then retry the upload."],
    )


def _http_json(
    method: str,
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float,
) -> ToolResult:
    safe_url = _redact_url(url)
    request = Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
    except HTTPError as error:
        return _error(
            "backend_error",
            f"HTTP {error.code} from LAN printer for {method} {safe_url}.",
            _http_suggestions(error.code),
        )
    except URLError as error:
        return _error(
            "connection_error",
            f"Could not reach LAN printer at {safe_url}: {error.reason}",
            ["Check the printer IP, LAN mode, and local network connectivity."],
        )
    try:
        payload: object = json.loads(body.decode("utf-8")) if body else {}
    except json.JSONDecodeError:
        return _error("backend_error", f"LAN printer returned invalid JSON from {safe_url}.", [])
    if not isinstance(payload, dict):
        return _error("backend_error", f"LAN printer returned non-object JSON from {safe_url}.", [])
    if payload.get("error_code") == 1000:
        return _error(
            "upload_error",
            "LAN printer rejected the access token.",
            ["Set ELEGOO_ACCESS_CODE or pass access_code for printers that require LAN auth."],
        )
    return ToolResult(ok=True, backend="elegoo", data=cast(JsonObject, payload))


def _decode_body_excerpt(body: bytes) -> str:
    if not body:
        return ""
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    return f" Response body: {text[:500]}"


def _redact_url(url: str) -> str:
    marker = "X-Token="
    if marker not in url:
        return url
    prefix, _, rest = url.partition(marker)
    end = len(rest)
    for separator in ("&", "#"):
        index = rest.find(separator)
        if index != -1:
            end = min(end, index)
    return f"{prefix}{marker}<redacted>{rest[end:]}"


def _http_suggestions(status_code: int) -> list[str]:
    if status_code == 401:
        return ["Set ELEGOO_ACCESS_CODE or pass access_code for LAN-auth printers."]
    if status_code == 403:
        return ["The printer denied access. Confirm LAN mode and auth settings on the printer."]
    if status_code == 429:
        return ["The printer is busy or rate limited. Wait and retry."]
    return ["Check the printer IP address and LAN mode."]


def _multipart_body(
    *,
    fields: dict[str, str],
    file_field: str,
    file_name: str,
    file_bytes: bytes,
) -> tuple[bytes, str]:
    boundary = f"----printing-mcp-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
                value.encode(),
                b"\r\n",
            ]
        )
    parts.extend(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'
            ).encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _file_md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_json(value: str | bytes) -> JsonValue:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    try:
        return cast(JsonValue, json.loads(value))
    except json.JSONDecodeError:
        return {"raw": value}


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _annotate_command_result(
    result: ToolResult,
    *,
    action: str,
    command: JsonObject,
    message: str,
) -> ToolResult:
    if not result.ok:
        return result
    data = result.data or {}
    data["action"] = action
    data["command"] = command
    result.data = data
    result.message = message
    return result


def _error(error_type: ErrorType, message: str, suggestions: list[str]) -> ToolResult:
    return ToolResult(
        ok=False,
        backend="elegoo",
        error_type=error_type,
        message=message,
        suggestions=suggestions,
    )
