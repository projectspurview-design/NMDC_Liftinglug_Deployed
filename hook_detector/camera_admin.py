from __future__ import annotations

import ipaddress
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from cryptography.fernet import Fernet, InvalidToken

DEFAULT_RTSP_PORT = 554
DEFAULT_RTSP_PATH = "/axis-media/media.amp?streamprofile=Profile_1"
_HOST_PATTERN = re.compile(r"^[A-Za-z0-9._:\-\[\]]+$")
_CAMERA_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DEFAULT_CAMERA_ID = "Bay-01-Camera-1"


def build_rtsp_url(configuration: dict[str, Any]) -> str:
    host = str(configuration.get("host", "")).strip()
    username = str(configuration.get("username", "")).strip()
    password = str(configuration.get("password", ""))
    rtsp_port = configuration.get("rtsp_port", DEFAULT_RTSP_PORT)
    rtsp_path = configuration.get("rtsp_path", DEFAULT_RTSP_PATH)

    if not host or len(host) > 253 or not _HOST_PATTERN.fullmatch(host):
        raise ValueError("Enter a valid camera hostname or IP address.")
    parsed_host = urlsplit(f"//{host}")
    if not parsed_host.hostname or parsed_host.username or parsed_host.password:
        raise ValueError("Enter a camera hostname or IP address without credentials.")
    if not username:
        raise ValueError("Enter the camera username.")
    if not password:
        raise ValueError("Enter the camera password.")
    if len(username) > 256 or len(password) > 1024:
        raise ValueError("Camera username or password is too long.")
    if any(ord(char) < 32 for char in username + password):
        raise ValueError("Camera username and password cannot contain control characters.")
    if isinstance(rtsp_port, bool) or not isinstance(rtsp_port, int) or not 1 <= rtsp_port <= 65535:
        raise ValueError("RTSP port must be an integer between 1 and 65535.")
    if not isinstance(rtsp_path, str) or len(rtsp_path) > 2048:
        raise ValueError("RTSP path must be text no longer than 2048 characters.")
    parsed_path = urlsplit(rtsp_path)
    if (
        not rtsp_path.startswith("/")
        or rtsp_path.startswith("//")
        or parsed_path.scheme
        or parsed_path.netloc
        or parsed_path.fragment
        or any(ord(char) < 32 for char in rtsp_path)
    ):
        raise ValueError("RTSP path must be a path on the configured camera.")

    netloc_host = f"[{parsed_host.hostname}]" if ":" in parsed_host.hostname else parsed_host.hostname
    credentials = f"{quote(username, safe='')}:{quote(password, safe='')}@"
    return f"rtsp://{credentials}{netloc_host}:{rtsp_port}{rtsp_path}"


def validate_camera_configuration(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Camera settings must be a JSON object.")
    source_type = payload.get("source_type", "rtsp")
    if source_type == "usb":
        camera_index = payload.get("device_index", 0)
        if isinstance(camera_index, bool) or not isinstance(camera_index, int) or not 0 <= camera_index <= 255:
            raise ValueError("Select a USB camera device index between 0 and 255.")
        return {"source_type": "usb", "device_index": camera_index}
    if source_type != "rtsp":
        raise ValueError("Camera source must be RTSP or USB.")

    host = payload.get("host")
    username = payload.get("username", "")
    password = payload.get("password", "")
    if not isinstance(host, str) or not isinstance(username, str) or not isinstance(password, str):
        raise ValueError("Camera address, username, and password must be text.")
    configuration = {
        "source_type": "rtsp",
        "host": host.strip(),
        "username": username.strip(),
        "password": password,
    }
    if "rtsp_port" in payload:
        configuration["rtsp_port"] = payload["rtsp_port"]
    if "rtsp_path" in payload:
        configuration["rtsp_path"] = payload["rtsp_path"]
    if "poe_switch_port" in payload:
        switch_port = payload["poe_switch_port"]
        if switch_port is not None:
            if not isinstance(switch_port, str) or len(switch_port) > 128:
                raise ValueError("PoE switch port must be text no longer than 128 characters.")
            if any(ord(char) < 32 for char in switch_port):
                raise ValueError("PoE switch port cannot contain control characters.")
            switch_port = switch_port.strip() or None
        configuration["poe_switch_port"] = switch_port
    build_rtsp_url(configuration)
    return configuration


def _public_camera_configuration(configuration: dict[str, Any]) -> dict[str, Any]:
    if configuration["source_type"] == "usb":
        return {
            "source_type": "usb",
            "device_index": configuration["device_index"],
        }
    public = {
        "source_type": "rtsp",
        "host": configuration["host"],
        "username": configuration["username"],
    }
    if "rtsp_port" in configuration:
        public["rtsp_port"] = configuration["rtsp_port"]
    if "rtsp_path" in configuration:
        public["rtsp_path"] = configuration["rtsp_path"]
    if "poe_switch_port" in configuration:
        public["poe_switch_port"] = configuration["poe_switch_port"]
    return public


class CameraAdminStore:
    def __init__(self, data_dir: str | Path | None = None) -> None:
        configured_dir = data_dir or os.getenv("HOOK_DETECTOR_DATA_DIR")
        self.data_dir = Path(configured_dir).expanduser() if configured_dir else Path.home() / ".nmdc-hook-detector"
        self.path = self.data_dir / "admin.json"
        self.key_path = self.data_dir / "camera.key"
        self._lock = threading.RLock()
        self._encryption_key = self._read_key()
        self._camera_configurations: dict[str, dict[str, Any]] = {}
        self._legacy_configuration_locked = False
        self._state = self._read_state()
        self._poe_assignments = self._read_poe_assignments()
        self._load_camera_configuration()

    def _read_state(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or state.get("version") not in {1, 2, 3}:
                raise ValueError
            return state
        except (OSError, json.JSONDecodeError, ValueError) as error:
            raise RuntimeError(f"Cannot read camera administrator data at {self.path}.") from error

    def _write_state(self, state: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.data_dir,
                prefix=".admin-",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = temporary_file.name
                json.dump(state, temporary_file)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self.path)
        except OSError:
            if temporary_path and os.path.exists(temporary_path):
                os.unlink(temporary_path)
            raise

    def _read_key(self) -> bytes | None:
        try:
            return self.key_path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise RuntimeError(f"Cannot read camera settings key at {self.key_path}.") from error

    def _load_camera_configuration(self) -> None:
        if self._state is None:
            return
        encrypted = self._state.get("camera_encrypted")
        if not encrypted:
            return
        if self._state.get("version") == 1:
            self._legacy_configuration_locked = True
            return
        if self._encryption_key is None:
            raise RuntimeError(f"Camera settings key is missing at {self.key_path}.")
        try:
            payload = Fernet(self._encryption_key).decrypt(encrypted.encode("ascii"))
            configuration = json.loads(payload.decode("utf-8"))
        except (InvalidToken, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError("Saved camera settings could not be decrypted.") from error
        if self._state.get("version") == 2:
            self._camera_configurations = {
                DEFAULT_CAMERA_ID: validate_camera_configuration(configuration)
            }
            return
        if not isinstance(configuration, dict):
            raise RuntimeError("Saved camera settings have an invalid format.")
        self._camera_configurations = {
            self._validate_camera_id(camera_id): validate_camera_configuration(camera)
            for camera_id, camera in configuration.items()
        }

    def _read_poe_assignments(self) -> dict[str, dict[str, str | None]]:
        if self._state is None:
            return {}
        raw = self._state.get("poe_assignments", {})
        if not isinstance(raw, dict):
            raise RuntimeError("Saved PoE assignments have an invalid format.")
        assignments: dict[str, dict[str, str | None]] = {}
        for camera_id, value in raw.items():
            try:
                camera_id = self._validate_camera_id(camera_id)
            except ValueError as error:
                raise RuntimeError("Saved PoE assignments have an invalid camera ID.") from error
            if not isinstance(value, dict):
                raise RuntimeError("Saved PoE assignments have an invalid format.")
            assignments[camera_id] = {
                "ip_address": value.get("ip_address"),
                "poe_switch_port": value.get("poe_switch_port"),
            }
        return assignments

    @property
    def camera_configured(self) -> bool:
        return DEFAULT_CAMERA_ID in self._camera_configurations

    @property
    def camera_configuration_available(self) -> bool:
        return self.camera_configured

    @property
    def configured_camera_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._camera_configurations)

    @property
    def needs_reconfiguration(self) -> bool:
        return self._legacy_configuration_locked

    @staticmethod
    def _validate_camera_id(camera_id: str) -> str:
        if not isinstance(camera_id, str) or not _CAMERA_ID_PATTERN.fullmatch(camera_id):
            raise ValueError("Camera ID must contain only letters, numbers, hyphens, or underscores.")
        return camera_id

    def public_camera_configuration(
        self, camera_id: str = DEFAULT_CAMERA_ID
    ) -> dict[str, Any] | None:
        with self._lock:
            configuration = self._camera_configurations.get(self._validate_camera_id(camera_id))
            if configuration is None:
                return None
            return _public_camera_configuration(configuration)

    def camera_configuration(self, camera_id: str = DEFAULT_CAMERA_ID) -> dict[str, Any]:
        with self._lock:
            configuration = self._camera_configurations.get(self._validate_camera_id(camera_id))
            if configuration is None:
                raise RuntimeError("Camera settings have not been configured.")
            return dict(configuration)

    def camera_rtsp_url(self, camera_id: str = DEFAULT_CAMERA_ID) -> str:
        with self._lock:
            configuration = self._camera_configurations.get(self._validate_camera_id(camera_id))
            if configuration is None:
                raise RuntimeError("Camera settings have not been configured.")
            if configuration["source_type"] != "rtsp":
                raise RuntimeError("This camera is configured as a USB camera, not RTSP.")
            return build_rtsp_url(configuration)

    def save_camera_configuration(
        self, payload: Any, camera_id: str = DEFAULT_CAMERA_ID
    ) -> tuple[dict[str, Any], str | None]:
        with self._lock:
            camera_id = self._validate_camera_id(camera_id)
            configuration = validate_camera_configuration(payload)
            if self._encryption_key is None:
                self.data_dir.mkdir(parents=True, exist_ok=True)
                self._encryption_key = Fernet.generate_key()
                self.key_path.write_bytes(self._encryption_key)
            updated_configurations = dict(self._camera_configurations)
            updated_configurations[camera_id] = configuration
            encrypted = Fernet(self._encryption_key).encrypt(
                json.dumps(updated_configurations, separators=(",", ":")).encode("utf-8")
            )
            updated_state = {"version": 3}
            updated_state["camera_encrypted"] = encrypted.decode("ascii")
            if self._poe_assignments:
                updated_state["poe_assignments"] = self._poe_assignments
            self._write_state(updated_state)
            self._state = updated_state
            self._camera_configurations = updated_configurations
            self._legacy_configuration_locked = False
            if configuration["source_type"] == "usb":
                rtsp_url = None
            else:
                rtsp_url = build_rtsp_url(configuration)
            public_configuration = _public_camera_configuration(configuration)
            return public_configuration, rtsp_url

    def remove_camera_configuration(self, camera_id: str) -> bool:
        with self._lock:
            camera_id = self._validate_camera_id(camera_id)
            if camera_id not in self._camera_configurations:
                return False
            updated_configurations = dict(self._camera_configurations)
            del updated_configurations[camera_id]
            if self._encryption_key is None:
                raise RuntimeError("Camera settings key is unavailable.")
            encrypted = Fernet(self._encryption_key).encrypt(
                json.dumps(updated_configurations, separators=(",", ":")).encode("utf-8")
            )
            updated_state = {
                "version": 3,
                "camera_encrypted": encrypted.decode("ascii"),
            }
            if self._poe_assignments:
                updated_state["poe_assignments"] = self._poe_assignments
            self._write_state(updated_state)
            self._state = updated_state
            self._camera_configurations = updated_configurations
            self._legacy_configuration_locked = False
            return True

    def _poe_assignment_id(self, bay_id: str, side: str) -> str:
        if not isinstance(bay_id, str) or not re.fullmatch(r"Bay-[0-9]{2}", bay_id):
            raise ValueError("bay_id must use the format Bay-01 through Bay-08.")
        bay_number = int(bay_id.rsplit("-", 1)[1])
        if not 1 <= bay_number <= 8:
            raise ValueError("bay_id must use Bay-01 through Bay-08.")
        if side not in {"left", "right"}:
            raise ValueError("side must be left or right.")
        camera_number = 1 if side == "left" else 2
        return f"{bay_id}-Camera-{camera_number}"

    def poe_assignments(self) -> dict[str, dict[str, str | None]]:
        with self._lock:
            assignments: dict[str, dict[str, str | None]] = {}
            for bay_number in range(1, 9):
                bay_id = f"Bay-{bay_number:02d}"
                for side in ("left", "right"):
                    camera_id = self._poe_assignment_id(bay_id, side)
                    configuration = self._camera_configurations.get(camera_id)
                    assignment = dict(self._poe_assignments.get(camera_id, {}))
                    assignments[camera_id] = {
                        "camera_id": camera_id,
                        "bay_id": bay_id,
                        "side": side,
                        "ip_address": assignment.get("ip_address")
                        or (configuration.get("host") if configuration else None),
                        "poe_switch_port": assignment.get("poe_switch_port")
                        or (configuration.get("poe_switch_port") if configuration else None),
                    }
            return assignments

    def save_poe_assignment(
        self,
        bay_id: str,
        side: str,
        ip_address: str,
        poe_switch_port: str | None,
    ) -> dict[str, str | None]:
        camera_id = self._poe_assignment_id(bay_id, side)
        if not isinstance(ip_address, str):
            raise ValueError("IP address must be text.")
        ip_address = ip_address.strip()
        try:
            normalized_ip = str(ipaddress.ip_address(ip_address))
        except ValueError as error:
            raise ValueError("Enter a valid IPv4 or IPv6 address.") from error
        if poe_switch_port is not None:
            if not isinstance(poe_switch_port, str) or len(poe_switch_port) > 128:
                raise ValueError("PoE switch port must be text no longer than 128 characters.")
            if any(ord(char) < 32 for char in poe_switch_port):
                raise ValueError("PoE switch port cannot contain control characters.")
            poe_switch_port = poe_switch_port.strip() or None

        with self._lock:
            updated_assignments = dict(self._poe_assignments)
            updated_assignments[camera_id] = {
                "ip_address": normalized_ip,
                "poe_switch_port": poe_switch_port,
            }

            state = dict(self._state or {"version": 3})
            state["version"] = 3
            state["poe_assignments"] = updated_assignments
            self._write_state(state)
            self._state = state
            self._poe_assignments = updated_assignments

            configuration = self._camera_configurations.get(camera_id)
            if configuration is not None:
                updated_configuration = dict(configuration)
                updated_configuration["host"] = normalized_ip
                updated_configuration["poe_switch_port"] = poe_switch_port
                updated_configurations = dict(self._camera_configurations)
                updated_configurations[camera_id] = updated_configuration
                if self._encryption_key is None:
                    raise RuntimeError("Camera settings key is unavailable.")
                encrypted = Fernet(self._encryption_key).encrypt(
                    json.dumps(updated_configurations, separators=(",", ":")).encode("utf-8")
                )
                state = {"version": 3, "camera_encrypted": encrypted.decode("ascii")}
                state["poe_assignments"] = updated_assignments
                self._write_state(state)
                self._state = state
                self._camera_configurations = updated_configurations

            return {
                "camera_id": camera_id,
                "bay_id": bay_id,
                "side": side,
                "ip_address": normalized_ip,
                "poe_switch_port": poe_switch_port,
            }
