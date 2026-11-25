from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from urllib.parse import unquote, urlsplit

import cv2

from .auth import SESSION_COOKIE_NAME, SESSION_MAX_AGE_SECONDS, AuthStore
from .camera_admin import DEFAULT_CAMERA_ID
from .camera_discovery import discover_onvif_cameras

def _available_usb_cameras() -> list[dict[str, int | str]]:
    if sys.platform == "win32":
        import comtypes

        comtypes.CoInitialize()
        try:
            from pygrabber.dshow_graph import FilterGraph

            device_names = FilterGraph().get_input_devices()
        finally:
            comtypes.CoUninitialize()
        return [
            {
                "device_index": device_index,
                "name": name,
            }
            for device_index, name in enumerate(device_names)
        ]

    cameras = []
    for device_index in range(32):
        if sys.platform.startswith("linux"):
            if not Path(f"/dev/video{device_index}").exists():
                continue
            capture = cv2.VideoCapture(device_index, cv2.CAP_V4L2)
        else:
            capture = cv2.VideoCapture(device_index)
        try:
            if capture.isOpened():
                cameras.append({
                    "device_index": device_index,
                    "name": f"USB camera {device_index}",
                })
        finally:
            capture.release()
    return cameras


class BackendHTTPServer:
    def __init__(
        self,
        processor,
        host: str = "0.0.0.0",
        port: int = 8001,
        auth_store: AuthStore | None = None,
    ):
        self.processor = processor
        self.host = host
        self.port = port
        self.auth_store = auth_store
        self._server = None
        self._thread = None

    def start(self):
        class Handler(BaseHTTPRequestHandler):
            def _send_json(self, payload, status: int = 200, headers: dict[str, str] | None = None):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self._send_cors_headers()
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            def _send_cors_headers(self):
                origin = self.headers.get("Origin")
                auth_store = getattr(self.server, "auth_store", None)
                if auth_store is None or origin not in auth_store.allowed_origins:
                    return
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Credentials", "true")
                self.send_header("Vary", "Origin")

            def _authorize(self, *, admin: bool = False, csrf: bool = False) -> bool:
                auth_store: AuthStore | None = getattr(self.server, "auth_store", None)
                if auth_store is None:
                    self._send_json({"error": "Authentication is unavailable."}, status=503)
                    return False
                cookie = SimpleCookie()
                try:
                    cookie.load(self.headers.get("Cookie", ""))
                except CookieError:
                    cookie = SimpleCookie()
                morsel = cookie.get(SESSION_COOKIE_NAME)
                session_token = morsel.value if morsel is not None else None
                session = auth_store.get_session(session_token)
                if session is None:
                    self._send_json({"error": "Authentication required."}, status=401)
                    return False
                if admin and session["role"] != "admin":
                    self._send_json({"error": "Admin role required."}, status=403)
                    return False
                if csrf and not auth_store.verify_csrf(
                    session,
                    self.headers.get("X-CSRF-Token"),
                ):
                    self._send_json({"error": "CSRF token is missing or invalid."}, status=403)
                    return False
                self.auth_session = session
                return True

            def _read_json(self):
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                except ValueError as error:
                    raise ValueError("Invalid request size.") from error
                if not 0 < content_length <= 16_384:
                    raise ValueError("Request body must be between 1 and 16384 bytes.")
                try:
                    payload = json.loads(self.rfile.read(content_length))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ValueError("Request body must contain valid JSON.") from error
                if not isinstance(payload, dict):
                    raise ValueError("Request body must be a JSON object.")
                return payload

            def _admin_store(self):
                store = getattr(self.server, "camera_admin", None)
                if store is None:
                    self._send_json({"error": "Camera settings are unavailable."}, status=503)
                return store

            def _camera_processor(self, camera_id: str):
                manager = getattr(self.server, "camera_manager", None)
                if manager is None:
                    if camera_id == DEFAULT_CAMERA_ID:
                        return self.server.processor
                    return None
                return manager.get_processor(camera_id)

            def _serve_snapshot(self, processor):
                body = getattr(processor, "latest_jpeg", None)
                if body is None:
                    frame = processor.latest_frame
                    if frame is None:
                        self.send_error(503, "No camera frame is available yet.")
                        return

                    ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                    if not ok:
                        self.send_error(500, "Failed to encode the camera frame.")
                        return
                    body = buffer.tobytes()
                if body is None:
                    self.send_error(503, "No camera frame is available yet.")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(body)

            def _serve_stream(self, processor):
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self._send_cors_headers()
                self.end_headers()
                last_frame = None
                last_sequence = None
                while True:
                    if getattr(getattr(processor, "stop_event", None), "is_set", lambda: False)():
                        return
                    frame = getattr(processor, "latest_jpeg", None)
                    sequence = getattr(processor, "jpeg_sequence", None)
                    is_new_frame = sequence != last_sequence if sequence is not None else frame is not last_frame
                    if frame is not None and is_new_frame:
                        try:
                            self.wfile.write(b"--frame\r\n")
                            self.wfile.write(b"Content-Type: image/jpeg\r\n\r\n")
                            self.wfile.write(frame)
                            self.wfile.write(b"\r\n")
                            self.wfile.flush()
                        except (ConnectionError, OSError):
                            return
                        last_frame = frame
                        last_sequence = sequence
                    time.sleep(0.05)

            def do_GET(self):
                path = unquote(urlsplit(self.path).path)
                if path == "/":
                    body = Path(__file__).with_name("detection_ui.html").read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if not self._authorize():
                    return
                from .archive_http import serve
                if serve(self, getattr(self.server, "camera_manager", None)):
                    return
                path_parts = [part for part in path.strip("/").split("/") if part]

                if path == "/api/auth/me":
                    self._send_json({
                        "user": {
                            "license_id": self.auth_session["license_id"],
                            "role": self.auth_session["role"],
                        },
                        "csrf_token": self.auth_session["csrf_token"],
                    })
                    return

                if path_parts == ["camera-admin", "usb-devices"]:
                    if not self._authorize(admin=True):
                        return
                    if self._admin_store() is None:
                        return
                    try:
                        devices = _available_usb_cameras()
                    except ImportError as error:
                        self._send_json(
                            {"error": "Windows camera discovery requires pygrabber. Install the app requirements and restart the detector."},
                            status=503,
                        )
                        return
                    self._send_json({"devices": devices})
                    return

                if path_parts[:2] == ["camera-admin", "config"] and len(path_parts) <= 3:
                    if not self._authorize(admin=True):
                        return
                    store = self._admin_store()
                    if store is None:
                        return
                    camera_id = path_parts[2] if len(path_parts) == 3 else DEFAULT_CAMERA_ID
                    try:
                        self._send_json({
                            "camera": store.public_camera_configuration(camera_id),
                            "needs_reconfiguration": store.needs_reconfiguration,
                        })
                    except ValueError as error:
                        self._send_json({"error": str(error)}, status=400)
                    return

                if len(path_parts) == 3 and path_parts[0] == "camera":
                    camera_id, resource = path_parts[1], path_parts[2]
                    manager = getattr(self.server, "camera_manager", None)
                    if manager is not None:
                        try:
                            if resource == "status":
                                self._send_json(manager.get_status(camera_id))
                                return
                            if resource == "health":
                                self._send_json(manager.get_health(camera_id))
                                return
                        except ValueError as error:
                            self._send_json({"error": str(error)}, status=400)
                            return
                    if resource in {"snapshot.jpg", "stream"}:
                        processor = self._camera_processor(camera_id)
                        if processor is None:
                            self.send_error(404, "Camera is not configured.")
                            return
                        if resource == "snapshot.jpg":
                            self._serve_snapshot(processor)
                        else:
                            self._serve_stream(processor)
                        return
                    self.send_error(404)
                    return

                if path == "/health":
                    processor = self.server.processor
                    self._send_json(processor.health_snapshot())
                    return

                if path == "/status":
                    processor = self.server.processor
                    self._send_json(processor.status_snapshot())
                    return

                if path == "/snapshot.jpg":
                    self._serve_snapshot(self.server.processor)
                    return

                if path == "/stream":
                    self._serve_stream(self.server.processor)
                    return

                self.send_error(404)

            def do_POST(self):
                path = unquote(urlsplit(self.path).path)
                path_parts = [part for part in path.strip("/").split("/") if part]
                if path == "/api/auth/login":
                    auth_store: AuthStore | None = getattr(self.server, "auth_store", None)
                    if auth_store is None:
                        self._send_json({"error": "Authentication is unavailable."}, status=503)
                        return
                    try:
                        payload = self._read_json()
                    except ValueError as error:
                        self._send_json({"error": str(error)}, status=400)
                        return
                    result = auth_store.login(payload.get("license_id"), payload.get("password"))
                    if result is None:
                        self._send_json({"error": "Invalid License ID or password."}, status=401)
                        return
                    account, session_token, csrf_token = result
                    cookie = SimpleCookie()
                    cookie[SESSION_COOKIE_NAME] = session_token
                    cookie[SESSION_COOKIE_NAME]["max-age"] = SESSION_MAX_AGE_SECONDS
                    cookie[SESSION_COOKIE_NAME]["httponly"] = True
                    cookie[SESSION_COOKIE_NAME]["secure"] = auth_store.cookie_secure
                    cookie[SESSION_COOKIE_NAME]["samesite"] = auth_store.cookie_samesite
                    cookie[SESSION_COOKIE_NAME]["path"] = "/"
                    self._send_json(
                        {
                            "user": {"license_id": account["license_id"], "role": account["role"]},
                            "csrf_token": csrf_token,
                        },
                        headers={"Set-Cookie": cookie[SESSION_COOKIE_NAME].OutputString()},
                    )
                    return

                if path == "/api/auth/logout":
                    if not self._authorize(csrf=True):
                        return
                    auth_store = getattr(self.server, "auth_store", None)
                    cookie = SimpleCookie()
                    try:
                        cookie.load(self.headers.get("Cookie", ""))
                    except CookieError:
                        cookie = SimpleCookie()
                    morsel = cookie.get(SESSION_COOKIE_NAME)
                    if auth_store is not None:
                        auth_store.logout(morsel.value if morsel is not None else None)
                    expired_cookie = SimpleCookie()
                    expired_cookie[SESSION_COOKIE_NAME] = ""
                    expired_cookie[SESSION_COOKIE_NAME]["max-age"] = 0
                    expired_cookie[SESSION_COOKIE_NAME]["httponly"] = True
                    expired_cookie[SESSION_COOKIE_NAME]["secure"] = bool(
                        auth_store and auth_store.cookie_secure
                    )
                    expired_cookie[SESSION_COOKIE_NAME]["samesite"] = (
                        auth_store.cookie_samesite if auth_store else "lax"
                    )
                    expired_cookie[SESSION_COOKIE_NAME]["path"] = "/"
                    self.send_response(204)
                    self.send_header("Set-Cookie", expired_cookie[SESSION_COOKIE_NAME].OutputString())
                    self.end_headers()
                    return

                if path == "/camera-admin/discover":
                    if not self._authorize(admin=True, csrf=True):
                        return
                    try:
                        devices = discover_onvif_cameras()
                    except OSError as error:
                        self._send_json({"error": f"ONVIF discovery failed: {error}"}, status=503)
                        return
                    self._send_json({"protocol": "onvif-ws-discovery", "devices": devices})
                    return

                if path_parts[:2] == ["camera-admin", "config"] and len(path_parts) <= 3:
                    if not self._authorize(admin=True, csrf=True):
                        return
                    store = self._admin_store()
                    if store is None:
                        return
                    camera_id = path_parts[2] if len(path_parts) == 3 else DEFAULT_CAMERA_ID
                    try:
                        payload = self._read_json()
                        if payload.get("action") == "reset" and set(payload) == {"action"}:
                            manager = getattr(self.server, "camera_manager", None)
                            if manager is not None:
                                manager.disconnect_camera(camera_id)
                            removed = store.remove_camera_configuration(camera_id)
                            self._send_json({"removed": removed, "camera_id": camera_id})
                            return
                        manager = getattr(self.server, "camera_manager", None)
                        if manager is None and (
                            camera_id != DEFAULT_CAMERA_ID
                            or payload.get("source_type", "rtsp") == "usb"
                        ):
                            self._send_json(
                                {"error": "USB or additional camera feeds require multi-camera support."},
                                status=503,
                            )
                            return
                        configuration, rtsp_url = store.save_camera_configuration(payload, camera_id)
                        if manager is not None:
                            manager.configure_camera(
                                camera_id,
                                store.camera_configuration(camera_id),
                            )
                        elif camera_id == DEFAULT_CAMERA_ID:
                            if rtsp_url is None:
                                raise RuntimeError("An RTSP URL is required for the primary camera.")
                            self.server.processor.set_camera_url(rtsp_url)
                        self._send_json({"camera": configuration, "saved": True})
                    except ValueError as error:
                        self._send_json({"error": str(error)}, status=400)
                    except RuntimeError as error:
                        self._send_json({"error": str(error)}, status=503)
                    except OSError:
                        self._send_json({"error": "Camera settings could not be saved. Check the app data folder permissions."}, status=500)
                    return

                if path == "/shutdown":
                    if not self._authorize(admin=True, csrf=True):
                        return
                    manager = getattr(self.server, "camera_manager", None)
                    if manager is not None:
                        manager.request_stop()
                    else:
                        self.server.processor.request_stop()
                    self.send_response(204)
                    self.end_headers()
                    return
                self.send_error(404)

            def do_DELETE(self):
                path = unquote(urlsplit(self.path).path)
                path_parts = [part for part in path.strip("/").split("/") if part]
                if len(path_parts) != 3 or path_parts[:2] != ["camera-admin", "config"]:
                    self.send_error(404)
                    return

                if not self._authorize(admin=True, csrf=True):
                    return

                store = self._admin_store()
                if store is None:
                    return
                camera_id = path_parts[2]
                try:
                    manager = getattr(self.server, "camera_manager", None)
                    if manager is not None:
                        manager.disconnect_camera(camera_id)
                    removed = store.remove_camera_configuration(camera_id)
                    self._send_json({"removed": removed, "camera_id": camera_id})
                except ValueError as error:
                    self._send_json({"error": str(error)}, status=400)
                except RuntimeError as error:
                    self._send_json({"error": str(error)}, status=503)
                except OSError:
                    self._send_json(
                        {"error": "Camera settings could not be reset. Check the app data folder permissions."},
                        status=500,
                    )

            def do_OPTIONS(self):
                origin = self.headers.get("Origin")
                auth_store: AuthStore | None = getattr(self.server, "auth_store", None)
                if origin and (auth_store is None or origin not in auth_store.allowed_origins):
                    self.send_error(403)
                    return
                self.send_response(204)
                if origin:
                    self._send_cors_headers()
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
                    self.send_header("Access-Control-Allow-Headers", "Content-Type, X-CSRF-Token")
                    self.send_header("Access-Control-Max-Age", "600")
                self.end_headers()

            def log_message(self, *args, **kwargs):
                return

        server = ThreadingHTTPServer((self.host, self.port), Handler)
        server.processor = self.processor
        server.camera_admin = getattr(self.processor, "camera_admin", None)
        server.camera_manager = getattr(self.processor, "camera_manager", None)
        server.auth_store = self.auth_store
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        return server


def start_backend_server(
    processor,
    host: str = "0.0.0.0",
    port: int = 8001,
    auth_store: AuthStore | None = None,
):
    server = BackendHTTPServer(processor, host=host, port=port, auth_store=auth_store)
    return server.start()
