import http.client
import json
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import pytest

from app import _wait_for_camera_configuration
from hook_detector.auth import AuthStore, SESSION_COOKIE_NAME
from hook_detector.camera_admin import (
    DEFAULT_CAMERA_ID,
    CameraAdminStore,
    build_rtsp_url,
    validate_camera_configuration,
)
from hook_detector.camera_manager import CameraFeedManager
from hook_detector.stream import HookStreamProcessor
from hook_detector.web_ui import BackendHTTPServer


def _test_admin_auth(directory):
    auth_store = AuthStore(directory, cookie_secure=False)
    auth_store.create_initial_admin("test-admin", "test-admin-password")
    _, token, csrf_token = auth_store.login("test-admin", "test-admin-password")
    return auth_store, {
        "Cookie": f"{SESSION_COOKIE_NAME}={token}",
        "X-CSRF-Token": csrf_token,
    }


def test_camera_credentials_are_encrypted_and_load_without_sign_in():
    configuration = {
        "host": "camera.example",
        "username": "operator",
        "password": "pw",
    }
    with tempfile.TemporaryDirectory() as directory:
        store = CameraAdminStore(directory)
        public_configuration, rtsp_url = store.save_camera_configuration(configuration)

        persisted = Path(directory, "admin.json").read_text(encoding="utf-8")
        assert "operator" not in persisted
        assert '"pw"' not in persisted
        assert Path(directory, "camera.key").is_file()
        assert public_configuration == {
            "source_type": "rtsp",
            "host": "camera.example",
            "username": "operator",
        }
        assert rtsp_url == build_rtsp_url(configuration)

        restarted_store = CameraAdminStore(directory)
        assert restarted_store.camera_configured
        assert restarted_store.public_camera_configuration() == public_configuration
        assert restarted_store.camera_rtsp_url() == rtsp_url


def test_custom_rtsp_port_and_path_are_saved_and_used():
    configuration = {
        "host": "camera.example",
        "username": "operator",
        "password": "pw",
        "rtsp_port": 8554,
        "rtsp_path": "/live/ch0?profile=main",
        "poe_switch_port": "Gi1/0/4",
    }
    with tempfile.TemporaryDirectory() as directory:
        store = CameraAdminStore(directory)
        public_configuration, rtsp_url = store.save_camera_configuration(configuration)

        assert rtsp_url == "rtsp://operator:pw@camera.example:8554/live/ch0?profile=main"
        assert public_configuration == {
            "source_type": "rtsp",
            "host": "camera.example",
            "username": "operator",
            "rtsp_port": 8554,
            "rtsp_path": "/live/ch0?profile=main",
            "poe_switch_port": "Gi1/0/4",
        }
        assert CameraAdminStore(directory).camera_rtsp_url() == rtsp_url


@pytest.mark.parametrize(
    "overrides",
    [
        {"rtsp_port": 0},
        {"rtsp_port": 65536},
        {"rtsp_port": True},
        {"rtsp_path": "rtsp://other-camera/live"},
        {"rtsp_path": "//other-camera/live"},
    ],
)
def test_invalid_custom_rtsp_settings_are_rejected(overrides):
    configuration = {
        "host": "camera.example",
        "username": "operator",
        "password": "pw",
        **overrides,
    }
    with pytest.raises(ValueError):
        validate_camera_configuration(configuration)


def test_legacy_password_protected_settings_require_one_time_reentry():
    with tempfile.TemporaryDirectory() as directory:
        legacy_state = {
            "version": 1,
            "salt": "legacy-salt",
            "password_check": "legacy-check",
            "camera_encrypted": "legacy-encrypted-camera",
        }
        Path(directory, "admin.json").write_text(json.dumps(legacy_state), encoding="utf-8")

        store = CameraAdminStore(directory)
        assert store.needs_reconfiguration
        assert store.public_camera_configuration() is None

        configuration = {"host": "camera.example", "username": "operator", "password": "pw"}
        store.save_camera_configuration(configuration)
        assert not store.needs_reconfiguration
        assert CameraAdminStore(directory).camera_rtsp_url() == build_rtsp_url(configuration)


def test_camera_settings_are_encrypted_and_persisted_independently():
    first_id = "Bay-01-Camera-1"
    second_id = "Bay-01-Camera-2"
    first = {"host": "192.0.2.10", "username": "operator1", "password": "pw1"}
    second = {"host": "192.0.2.11", "username": "operator2", "password": "pw2"}

    with tempfile.TemporaryDirectory() as directory:
        store = CameraAdminStore(directory)
        store.save_camera_configuration(first, first_id)
        store.save_camera_configuration(second, second_id)

        persisted = Path(directory, "admin.json").read_text(encoding="utf-8")
        assert "operator1" not in persisted
        assert "operator2" not in persisted
        assert "pw1" not in persisted
        assert "pw2" not in persisted

        restarted_store = CameraAdminStore(directory)
        assert restarted_store.configured_camera_ids == (first_id, second_id)
        assert restarted_store.public_camera_configuration(first_id) == {
            "source_type": "rtsp",
            "host": first["host"],
            "username": first["username"],
        }
        assert restarted_store.public_camera_configuration(second_id) == {
            "source_type": "rtsp",
            "host": second["host"],
            "username": second["username"],
        }
        assert restarted_store.camera_rtsp_url(first_id) == build_rtsp_url(first)
        assert restarted_store.camera_rtsp_url(second_id) == build_rtsp_url(second)

        assert store.remove_camera_configuration(first_id)
        assert store.public_camera_configuration(first_id) is None
        assert store.public_camera_configuration(second_id) is not None
        assert CameraAdminStore(directory).configured_camera_ids == (second_id,)
        assert not store.remove_camera_configuration(first_id)


def test_usb_camera_configuration_persists_without_network_credentials():
    camera_id = "Bay-03-Camera-2"
    configuration = {"source_type": "usb", "device_index": 2}
    with tempfile.TemporaryDirectory() as directory:
        store = CameraAdminStore(directory)
        public_configuration, rtsp_url = store.save_camera_configuration(configuration, camera_id)

        assert public_configuration == configuration
        assert rtsp_url is None
        assert store.camera_configuration(camera_id) == configuration
        with pytest.raises(RuntimeError, match="USB camera"):
            store.camera_rtsp_url(camera_id)

        reloaded_store = CameraAdminStore(directory)
        assert reloaded_store.public_camera_configuration(camera_id) == configuration
        assert reloaded_store.camera_configuration(camera_id) == configuration


@pytest.mark.parametrize(
    "configuration",
    [
        {"source_type": "usb", "device_index": -1},
        {"source_type": "usb", "device_index": 256},
        {"source_type": "usb", "device_index": True},
        {"source_type": "usb", "device_index": "0"},
        {"source_type": "serial"},
    ],
)
def test_invalid_camera_source_settings_are_rejected(configuration):
    with pytest.raises(ValueError):
        validate_camera_configuration(configuration)


def test_frontend_waits_for_camera_settings_instead_of_using_default_stream():
    with tempfile.TemporaryDirectory() as directory:
        store = CameraAdminStore(directory)
        processor = SimpleNamespace(stop_event=threading.Event())
        result = []
        waiter = threading.Thread(
            target=lambda: result.append(_wait_for_camera_configuration(processor, store))
        )
        waiter.start()
        waiter.join(timeout=0.05)

        assert waiter.is_alive()
        store.save_camera_configuration({
            "host": "camera.example",
            "username": "operator",
            "password": "pw",
        })
        waiter.join(timeout=1)

        assert not waiter.is_alive()
        assert result == [True]


@pytest.mark.parametrize(
    "payload",
    [
        {"host": "camera.example/path", "username": "operator", "password": "pw"},
        {"host": "user@camera.example", "username": "operator", "password": "pw"},
        {"host": "camera.example", "username": "", "password": "pw"},
        {"host": "camera.example", "username": "operator", "password": ""},
        {
            "camera_id": "../camera",
            "host": "camera.example",
            "username": "operator",
            "password": "pw",
        },
    ],
)
def test_invalid_camera_addresses_are_rejected(payload):
    if "camera_id" in payload:
        with pytest.raises(ValueError):
            CameraAdminStore._validate_camera_id(payload["camera_id"])
    else:
        with pytest.raises(ValueError):
            validate_camera_configuration(payload)


def test_updating_camera_url_marks_stream_for_reconnect():
    processor = HookStreamProcessor.__new__(HookStreamProcessor)
    processor._camera_settings_lock = threading.Lock()
    processor._camera_generation = 0

    processor.set_camera_url("rtsp://camera.example:554/stream")

    assert processor.rtsp_url == "rtsp://camera.example:554/stream"
    assert processor._camera_generation == 1


def test_updating_camera_source_selects_usb_device_and_marks_stream_for_reconnect():
    processor = HookStreamProcessor.__new__(HookStreamProcessor)
    processor._camera_settings_lock = threading.Lock()
    processor._camera_generation = 0
    processor.rtsp_url = ""
    processor.source_type = "rtsp"
    processor.camera_index = 0

    processor.set_camera_source("webcam", camera_index=3)

    assert processor.source_type == "webcam"
    assert processor.camera_index == 3
    assert processor._camera_generation == 1


def test_usb_capture_opens_selected_device_index_without_rtsp_or_fallback():
    processor = HookStreamProcessor.__new__(HookStreamProcessor)
    processor.source_type = "rtsp"
    processor.camera_index = 0
    processor.allow_webcam_fallback = False
    processor.active_source = ""

    with (
        patch("hook_detector.stream.sys.platform", "linux"),
        patch("hook_detector.stream.os.path.exists", return_value=True),
        patch("hook_detector.stream.cv2.VideoCapture") as video_capture,
    ):
        video_capture.return_value.isOpened.return_value = True
        capture = processor._resolve_capture(source_type="webcam", camera_index=2)

    video_capture.assert_called_once_with(2, cv2.CAP_V4L2)
    capture.set.assert_called_once_with(
        cv2.CAP_PROP_BUFFERSIZE,
        1,
    )
    assert processor.active_source == "USB camera 2"


def test_linux_usb_capture_skips_missing_device_node():
    processor = HookStreamProcessor.__new__(HookStreamProcessor)
    processor.source_type = "webcam"
    processor.camera_index = 0
    processor.active_source = ""

    with (
        patch("hook_detector.stream.sys.platform", "linux"),
        patch("hook_detector.stream.os.path.exists", return_value=False),
        patch("hook_detector.stream.cv2.VideoCapture") as video_capture,
    ):
        capture = processor._resolve_capture(source_type="webcam", camera_index=0)

    video_capture.assert_called_once_with()
    capture.isOpened.assert_not_called()
    assert processor.active_source == "USB camera 0"


def test_usb_capture_uses_directshow_on_windows():
    processor = HookStreamProcessor.__new__(HookStreamProcessor)
    processor.source_type = "rtsp"
    processor.camera_index = 0
    processor.allow_webcam_fallback = False
    processor.active_source = ""

    with (
        patch("hook_detector.stream.sys.platform", "win32"),
        patch("hook_detector.stream.cv2.VideoCapture") as video_capture,
    ):
        video_capture.return_value.isOpened.return_value = True
        processor._resolve_capture(source_type="webcam", camera_index=2)

    video_capture.assert_called_once_with(2, cv2.CAP_DSHOW)


class FakeProcessor:
    def __init__(self):
        self.rtsp_url = None
        self.source_type = "rtsp"
        self.camera_index = 0
        self.latest_jpeg = None
        self.latest_frame = None

    def set_camera_url(self, rtsp_url):
        self.rtsp_url = rtsp_url

    def set_camera_source(self, source_type, camera_index=0, rtsp_url=None):
        self.source_type = source_type
        self.camera_index = camera_index
        if rtsp_url is not None:
            self.rtsp_url = rtsp_url

    def health_snapshot(self):
        return {"ok": True}

    def status_snapshot(self):
        return {"camera_connected": False}

    def request_stop(self):
        pass


class FakeWorkerProcessor(FakeProcessor):
    def __init__(self):
        super().__init__()
        self.stop_event = threading.Event()
        self.started = threading.Event()
        self.show_window = False
        self.enable_tts = False

    def run(self):
        self.started.set()
        self.stop_event.wait()

    def stop(self):
        self.stop_event.set()

    def _mark_camera_unavailable(self, message):
        self.latest_jpeg = None
        self.latest_frame = None
        self.camera_error = message
        self.camera_connected = False

    def request_stop(self):
        self.stop_event.set()

    def status_snapshot(self):
        return {"camera_connected": True, "camera_error": None}

    def health_snapshot(self):
        return {"ok": True, "camera_connected": True}


def test_camera_settings_http_routes_are_direct_and_never_return_password():
    with tempfile.TemporaryDirectory() as directory:
        processor = FakeProcessor()
        processor.camera_admin = CameraAdminStore(directory)
        auth_store, auth_headers = _test_admin_auth(directory)
        backend_server = BackendHTTPServer(processor, host="127.0.0.1", port=0, auth_store=auth_store)
        server = backend_server.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        configuration = {"host": "192.0.2.10", "username": "operator", "password": "pw"}
        try:
            connection.request("GET", "/camera-admin/config", headers=auth_headers)
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read()) == {"camera": None, "needs_reconfiguration": False}

            connection.request(
                "POST",
                "/camera-admin/config",
                json.dumps(configuration),
                {"Content-Type": "application/json", **auth_headers},
            )
            response = connection.getresponse()
            assert response.status == 200
            saved = json.loads(response.read())
            assert response.getheader("Cache-Control") == "no-store"
            assert saved["camera"] == {
                "source_type": "rtsp",
                "host": "192.0.2.10",
                "username": "operator",
            }
            assert response.getheader("Set-Cookie") is None
            assert processor.rtsp_url == build_rtsp_url(configuration)

            connection.request("GET", "/camera-admin/config", headers=auth_headers)
            response = connection.getresponse()
            assert response.status == 200
            loaded = json.loads(response.read())
            assert loaded["camera"] == {
                "source_type": "rtsp",
                "host": "192.0.2.10",
                "username": "operator",
            }
            assert "password" not in loaded["camera"]
        finally:
            connection.close()
            server.shutdown()
            backend_server._thread.join(timeout=2)
            server.server_close()


def test_usb_camera_scan_route_lists_all_directshow_devices():
    from types import ModuleType

    class FakeFilterGraph:
        def get_input_devices(self):
            return ["IPEVO DO-CAM", "USB Camera"]

    pygrabber = ModuleType("pygrabber")
    pygrabber.__path__ = []
    dshow_graph = ModuleType("pygrabber.dshow_graph")
    dshow_graph.FilterGraph = FakeFilterGraph

    with tempfile.TemporaryDirectory() as directory:
        processor = FakeProcessor()
        processor.camera_admin = CameraAdminStore(directory)
        auth_store, auth_headers = _test_admin_auth(directory)
        backend_server = BackendHTTPServer(processor, host="127.0.0.1", port=0, auth_store=auth_store)
        server = backend_server.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        try:
            with (
                patch.dict("sys.modules", {
                    "pygrabber": pygrabber,
                    "pygrabber.dshow_graph": dshow_graph,
                }),
                patch("hook_detector.web_ui.sys.platform", "win32"),
                patch("comtypes.CoInitialize") as initialize,
                patch("comtypes.CoUninitialize") as uninitialize,
            ):
                connection.request("GET", "/camera-admin/usb-devices", headers=auth_headers)
                response = connection.getresponse()
                assert response.status == 200
                assert response.getheader("Content-Type").startswith("application/json")
                assert json.loads(response.read()) == {
                    "devices": [
                        {"device_index": 0, "name": "IPEVO DO-CAM"},
                        {"device_index": 1, "name": "USB Camera"},
                    ],
                }
                initialize.assert_called_once_with()
                uninitialize.assert_called_once_with()
        finally:
            connection.close()
            server.shutdown()
            backend_server._thread.join(timeout=2)
            server.server_close()


def test_resetting_primary_camera_allows_a_fresh_worker_on_reconnect():
    with tempfile.TemporaryDirectory() as directory:
        primary = FakeWorkerProcessor()
        store = CameraAdminStore(directory)
        manager = CameraFeedManager(
            primary,
            store,
            processor_factory=lambda camera_id: FakeWorkerProcessor(),
        )
        try:
            store.save_camera_configuration({
                "host": "camera.example",
                "username": "operator",
                "password": "first",
            })
            manager.configure_camera(DEFAULT_CAMERA_ID, store.camera_configuration())
            assert primary.started.wait(timeout=1)

            manager.disconnect_camera(DEFAULT_CAMERA_ID)
            assert manager.get_processor(DEFAULT_CAMERA_ID) is None

            store.save_camera_configuration({
                "host": "camera.example",
                "username": "operator",
                "password": "second",
            })
            manager.configure_camera(DEFAULT_CAMERA_ID, store.camera_configuration())
            reconnected = manager.get_processor(DEFAULT_CAMERA_ID)
            assert reconnected is not None
            assert reconnected is not primary
            assert reconnected.started.wait(timeout=1)
        finally:
            manager.stop()


def test_per_camera_settings_route_starts_matching_camera_feed():
    with tempfile.TemporaryDirectory() as directory:
        primary = FakeWorkerProcessor()
        store = CameraAdminStore(directory)
        manager = CameraFeedManager(
            primary,
            store,
            processor_factory=lambda camera_id: FakeWorkerProcessor(),
        )
        primary.camera_manager = manager
        primary.camera_admin = store
        auth_store, auth_headers = _test_admin_auth(directory)
        backend_server = BackendHTTPServer(primary, host="127.0.0.1", port=0, auth_store=auth_store)
        server = backend_server.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        camera_id = "Bay-02-Camera-2"
        configuration = {
            "host": "192.0.2.12",
            "username": "operator",
            "password": "pw",
            "rtsp_port": 8554,
            "rtsp_path": "/live/ch0",
        }
        second_camera_id = "Bay-02-Camera-1"
        second_configuration = {"host": "192.0.2.13", "username": "operator2", "password": "pw2"}
        try:
            connection.request("GET", f"/camera-admin/config/{camera_id}", headers=auth_headers)
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read())["camera"] is None

            connection.request(
                "POST",
                f"/camera-admin/config/{camera_id}",
                json.dumps(configuration),
                {"Content-Type": "application/json", **auth_headers},
            )
            response = connection.getresponse()
            assert response.status == 200
            saved = json.loads(response.read())
            assert saved["camera"] == {
                "source_type": "rtsp",
                "host": configuration["host"],
                "username": configuration["username"],
                "rtsp_port": 8554,
                "rtsp_path": "/live/ch0",
            }
            assert "password" not in saved["camera"]

            worker = manager.get_processor(camera_id)
            assert worker is not None
            assert worker.rtsp_url == build_rtsp_url(configuration)
            assert worker.started.wait(timeout=1)

            connection.request("GET", f"/camera/{camera_id}/status", headers=auth_headers)
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read())["camera_connected"] is True

            connection.request(
                "POST",
                f"/camera-admin/config/{second_camera_id}",
                json.dumps(second_configuration),
                {"Content-Type": "application/json", **auth_headers},
            )
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read())["saved"] is True
            second_worker = manager.get_processor(second_camera_id)
            assert second_worker is not None
            assert second_worker is not worker
            assert second_worker.rtsp_url == build_rtsp_url(second_configuration)
            assert second_worker.started.wait(timeout=1)

            connection.request("GET", f"/camera-admin/config/{camera_id}", headers=auth_headers)
            response = connection.getresponse()
            loaded = json.loads(response.read())
            assert loaded["camera"]["host"] == configuration["host"]
            assert "password" not in loaded["camera"]
            connection.request("GET", f"/camera-admin/config/{second_camera_id}", headers=auth_headers)
            response = connection.getresponse()
            loaded = json.loads(response.read())
            assert loaded["camera"]["host"] == second_configuration["host"]
            assert loaded["camera"]["username"] == second_configuration["username"]
            assert "password" not in loaded["camera"]

            usb_camera_id = "Bay-03-Camera-1"
            connection.request(
                "POST",
                f"/camera-admin/config/{usb_camera_id}",
                json.dumps({"source_type": "usb", "device_index": 2}),
                {"Content-Type": "application/json", **auth_headers},
            )
            response = connection.getresponse()
            assert response.status == 200
            saved = json.loads(response.read())
            assert saved["camera"] == {"source_type": "usb", "device_index": 2}
            usb_worker = manager.get_processor(usb_camera_id)
            assert usb_worker is not None
            assert usb_worker.source_type == "webcam"
            assert usb_worker.camera_index == 2
            assert usb_worker.started.wait(timeout=1)

            connection.request(
                "POST",
                f"/camera-admin/config/{camera_id}",
                json.dumps({"action": "reset"}),
                {"Content-Type": "application/json", **auth_headers},
            )
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read()) == {"removed": True, "camera_id": camera_id}
            assert manager.get_processor(camera_id) is None
            assert not worker.started.is_set() or worker.stop_event.is_set()

            connection.request("GET", f"/camera-admin/config/{camera_id}", headers=auth_headers)
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read())["camera"] is None

            connection.request("GET", f"/camera/{camera_id}/status", headers=auth_headers)
            response = connection.getresponse()
            assert response.status == 200
            reset_status = json.loads(response.read())
            assert not reset_status["camera_connected"]
            assert "save this camera" in reset_status["camera_error"].lower()

            connection.request(
                "POST",
                f"/camera-admin/config/{camera_id}",
                json.dumps(configuration),
                {"Content-Type": "application/json", **auth_headers},
            )
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read())["saved"] is True
            reconnected_worker = manager.get_processor(camera_id)
            assert reconnected_worker is not None
            assert reconnected_worker is not worker
            assert reconnected_worker.started.wait(timeout=1)
        finally:
            connection.close()
            manager.stop()
            server.shutdown()
            backend_server._thread.join(timeout=2)
            server.server_close()
