from __future__ import annotations

import logging
from math import hypot
import os
import sys
import threading
import time
from collections.abc import Sequence
from typing import Any, Optional

import cv2
import psutil
from .deployment import SETTINGS

from .classifier import classify_hook_position
from .config import (
    BLUE,
    LIFTING_LUG_VISIBLE_TEXT,
    DEFAULT_RTSP_URL,
    GREEN,
    LOCK_CLASS,
    CONTAINER_CLASS,
    STATE_HOOK_LOCK_ENGAGED,
    STATE_HOOK_LOCK_NOT_ENGAGED,
    STATE_HOOK_LOCK_CONTACT_NOT_READY,
    STATE_HOOK_INSIDE_LUG,
    STATE_HOOK_OUTSIDE_LUG,
    STATUS_TEXT,
    TTS_LUG_VISIBLE_DELAY_SECONDS,
    TTS_LUG_VISIBLE_REPEAT_COUNT,
    TTS_LOCATION_ALERT_INTERVAL_SECONDS,
    TTS_LOCATION_STABLE_DELAY_SECONDS,
    YELLOW,
)
from .detector import HookYOLODetector
from .recording import DetectionVideoArchive
from .tts import TTSManager

LOGGER = logging.getLogger(__name__)

_CPU_SAMPLE_LOCK = threading.Lock()
_CPU_SAMPLE_AT = 0.0
_CPU_PERCENT = None
_NPU_SAMPLE_LOCK = threading.Lock()
_NPU_SAMPLE_AT = 0.0
_NPU_METRICS = None


def _system_cpu_percent() -> float | None:
    global _CPU_SAMPLE_AT, _CPU_PERCENT
    with _CPU_SAMPLE_LOCK:
        now = time.monotonic()
        if now - _CPU_SAMPLE_AT >= 1.0:
            value = psutil.cpu_percent(interval=None)
            if _CPU_SAMPLE_AT:
                _CPU_PERCENT = value
            _CPU_SAMPLE_AT = now
        return _CPU_PERCENT


def _npu_utilization_snapshot() -> dict[str, object]:
    global _NPU_SAMPLE_AT, _NPU_METRICS
    with _NPU_SAMPLE_LOCK:
        now = time.monotonic()
        if now - _NPU_SAMPLE_AT >= 1.0:
            try:
                from dx_engine import DeviceStatus

                devices = []
                for device_id in range(DeviceStatus.get_device_count()):
                    status: Any = DeviceStatus.get_current_status(device_id)
                    if not status.is_valid():
                        continue
                    core_utilization = []
                    for core_id in range(16):
                        utilization = status.get_core_utilization(core_id)
                        if utilization < 0:
                            break
                        core_utilization.append(round(utilization, 1))
                    if core_utilization:
                        devices.append({
                            "device_id": device_id,
                            "utilization_percent": round(sum(core_utilization) / len(core_utilization), 1),
                            "core_utilization_percent": core_utilization,
                        })
                all_cores = [
                    utilization
                    for device in devices
                    for utilization in device["core_utilization_percent"]
                ]
                _NPU_METRICS = {
                    "npu_utilization_percent": round(sum(all_cores) / len(all_cores), 1) if all_cores else None,
                    "npu_devices": devices,
                }
            except Exception:
                _NPU_METRICS = {"npu_utilization_percent": None, "npu_devices": []}
            _NPU_SAMPLE_AT = now
        return _NPU_METRICS or {"npu_utilization_percent": None, "npu_devices": []}

PREVIEW_MAX_WIDTH = 960
PREVIEW_FPS = 6
PREVIEW_JPEG_QUALITY = 85
HOOK_LOCK_CONTACT_GAP_RATIO = 0.15
HOOK_LOCK_PROGRESS_DISTANCE_RATIO = 4.0
HOOK_LOCK_COMMAND_TEXT = {
    STATE_HOOK_LOCK_ENGAGED: "Hook is engaged with lock. Ready to lift.",
    STATE_HOOK_LOCK_NOT_ENGAGED: "Hook is not engaged with lock. Do not lift.",
}


def _open_webcam_capture(camera_index: int):
    if sys.platform == "win32":
        return cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    if sys.platform.startswith("linux"):
        if not os.path.exists(f"/dev/video{camera_index}"):
            return cv2.VideoCapture()
        return cv2.VideoCapture(camera_index, cv2.CAP_V4L2)
    return cv2.VideoCapture(camera_index)


class CameraUnavailableError(RuntimeError):
    """Raised when a live camera source stops providing frames."""


class HookStreamProcessor:
    def __init__(
        self,
        model_path: str | Sequence[str],
        rtsp_url: Optional[str] = None,
        source_type: str = "rtsp",
        video_path: str = "",
        camera_index: int = 0,
        enable_tts: bool = True,
        conf: float = 0.25,
        show_window: bool = True,
        detector: HookYOLODetector | None = None,
        camera_side: str | None = None,
        tts_manager: TTSManager | None = None,
        video_archive: DetectionVideoArchive | None = None,
        camera_id: str | None = None,
    ) -> None:
        self.model_path = model_path
        self.rtsp_url = rtsp_url or os.getenv("RTSP_URL", DEFAULT_RTSP_URL)
        self._camera_settings_lock = threading.Lock()
        self._camera_generation = 0
        self.source_type = source_type
        self.video_path = video_path
        self.camera_index = camera_index
        self.allow_webcam_fallback = True
        self.enable_tts = enable_tts or tts_manager is not None
        self.show_window = show_window
        self.stop_event = threading.Event()
        self.detector = detector or HookYOLODetector(model_path=model_path, conf=conf)
        self.tts = tts_manager if tts_manager is not None else TTSManager() if enable_tts else None
        self._owns_tts = enable_tts and tts_manager is None
        self.camera_side = camera_side
        self.camera_id = camera_id or "camera"
        self.video_archive = video_archive
        self._recording_writer = None
        self._recording_path = None
        self._recording_last_frame_at = None
        self._tts_channel: object = id(self)
        self._tts_lug_visible_since = None
        self._tts_stable_since = None
        self._tts_location_state = None
        self._tts_location_last_alert = None
        self._tts_hook_missing_alerted = False
        self._tts_hook_lock_state = None
        self.last_status = "Initializing"
        self.latest_frame = None
        self.latest_jpeg = None
        self.jpeg_sequence = 0
        self._preview_overlay = None
        self._preview_lock_box = None
        self.active_source = source_type
        self.camera_connected = False
        self.camera_error = None
        self.retrying = False
        self._inference_latency_ms = None
        self._inference_interval_s = None
        self._last_inference_at = None
        self.state = "-"
        self.severity = "-"
        self.hook = "-"
        self.inserted = "-"
        self.lift = "-"
        self.offset = "-"
        self.updated = "-"
        self.alert_main = "Waiting for Lifting Hook Detector data..."
        self.alert_state = "-"
        self.meter = 0
        self.state_key = "-"
        self.hook_engaged = False
        self.hook_lock_contact = False
        self.hook_engagement_percent = 0
        self.lock_engagement_percent = 0
        self.hook_lock_state = None
        self.hook_lock_command = "Waiting for camera frame."
        self.hook_lock_command_hi = ""
        self.lift_ready = False
        self.cavity_detected = False
        self.hook_detected = False
        self.lock_detected = False
        self.alert_message_hi = ""

    def health_snapshot(self) -> dict[str, object]:
        return {
            "ok": True,
            "camera_connected": self.camera_connected,
            "frame_ready": self.latest_frame is not None,
            "camera_source": self.active_source,
            "camera_error": self.camera_error,
            "retrying": self.retrying,
        }

    def status_snapshot(self) -> dict[str, object]:
        age = time.monotonic() - getattr(self, "_result_at", 0)
        model_paths = getattr(self.detector, "model_paths", [])
        model_files = [os.path.basename(str(path)) for path in model_paths]
        extensions = {os.path.splitext(path)[1].lower() for path in model_files}
        if extensions == {".dxnn"}:
            inference_backend = "DXNN"
        elif extensions == {".pt"}:
            inference_backend = "PT"
        elif extensions:
            inference_backend = "MIXED"
        else:
            inference_backend = "UNKNOWN"
        inference_details = {
            "inference_backend": inference_backend,
            "model_files": model_files,
            "ai_box_cpu_percent": _system_cpu_percent(),
            **_npu_utilization_snapshot(),
            "inference_latency_ms": getattr(self, "_inference_latency_ms", None),
            "inference_fps": (
                round(1 / inference_interval_s, 2)
                if isinstance(
                    inference_interval_s := getattr(self, "_inference_interval_s", None),
                    (int, float),
                )
                and inference_interval_s > 0
                else None
            ),
        }
        if self.camera_connected and age > SETTINGS["stale_seconds"] and getattr(self, "_result_at", 0):
            self._preview_overlay = None
            if self.tts:
                self.tts.cancel_pending(channel=self._tts_channel)
            return {
                **inference_details,
                "status": "Detection stale",
                "state": "Unknown",
                "state_key": "UNKNOWN",
                "camera_connected": self.camera_connected,
                "lift_ready": False,
                "hook_engaged": False,
                "hook_lock_contact": False,
                "hook_engagement_percent": 0,
                "lock_engagement_percent": 0,
                "hook_lock_state": None,
                "hook_lock_command": "Detection stale. Do not lift.",
                "hook_lock_command_hi": "डेटा पुराना है। लिफ्ट न करें।",
                "lift": "Unverified",
                "meter": 0,
                "alert_main": "Detection stale",
                "detection_age_s": age,
                "frame_ready": self.latest_frame is not None,
            }
        return {
            **inference_details,
            "detection_age_s": age if getattr(self,"_result_at",0) else None,
            "status": self.last_status,
            "frame_ready": self.latest_frame is not None,
            "camera_connected": self.camera_connected,
            "camera_source": self.active_source,
            "camera_error": self.camera_error,
            "retrying": self.retrying,
            "state": self.state,
            "severity": self.severity,
            "hook": self.hook,
            "inserted": self.inserted,
            "lift": self.lift,
            "offset": self.offset,
            "updated": self.updated,
            "alert_main": self.alert_main,
            "alert_state": self.alert_state,
            "meter": self.meter,
            "state_key": self.state_key,
            "hook_engaged": self.hook_engaged,
            "hook_lock_contact": self.hook_lock_contact,
            "hook_engagement_percent": self.hook_engagement_percent,
            "lock_engagement_percent": self.lock_engagement_percent,
            "hook_lock_state": self.hook_lock_state,
            "hook_lock_command": self.hook_lock_command,
            "hook_lock_command_hi": self.hook_lock_command_hi,
            "lift_ready": self.lift_ready,
            "cavity_detected": self.cavity_detected,
            "hook_detected": self.hook_detected,
            "lock_detected": self.lock_detected,
            "alert_message_hi": self.alert_message_hi,
        }

    def _mark_camera_unavailable(self, message: str) -> None:
        self.camera_connected = False
        self.camera_error = message
        self.latest_frame = None
        self.latest_jpeg = None
        self._preview_overlay = None
        self._preview_lock_box = None
        self.last_status = message
        self.state = "-"
        self.severity = "-"
        self.hook = "-"
        self.inserted = "-"
        self.lift = "-"
        self.offset = "-"
        self.updated = "-"
        self.alert_main = message
        self.alert_state = "-"
        self.meter = 0
        self.state_key = "-"
        self.hook_engaged = False
        self.hook_lock_contact = False
        self.hook_engagement_percent = 0
        self.lock_engagement_percent = 0
        self.hook_lock_state = None
        self.hook_lock_command = "Waiting for camera frame."
        self.hook_lock_command_hi = ""
        self.lift_ready = False
        self.cavity_detected = False
        self.hook_detected = False
        self.lock_detected = False
        self.alert_message_hi = ""
        self._tts_hook_lock_state = None
        self._tts_lug_visible_since = None
        self._tts_stable_since = None
        self._tts_location_state = None
        self._tts_location_last_alert = None
        self._tts_hook_missing_alerted = False
        tts = getattr(self, "tts", None)
        if tts is not None:
            tts.cancel_pending(channel=getattr(self, "_tts_channel", id(self)))

    def set_camera_url(self, rtsp_url: str) -> None:
        self.set_camera_source("rtsp", rtsp_url=rtsp_url)

    def set_camera_source(
        self,
        source_type: str,
        camera_index: int = 0,
        rtsp_url: str | None = None,
    ) -> None:
        if source_type not in {"rtsp", "webcam"}:
            raise ValueError("Camera source must be RTSP or webcam.")
        if source_type == "rtsp" and not rtsp_url:
            raise ValueError("An RTSP URL is required for an RTSP camera.")
        if isinstance(camera_index, bool) or not isinstance(camera_index, int) or camera_index < 0:
            raise ValueError("USB camera index must be a non-negative integer.")
        with self._camera_settings_lock:
            self.source_type = source_type
            self.camera_index = camera_index
            if rtsp_url is not None:
                self.rtsp_url = rtsp_url
            self._camera_generation += 1

    def _resolve_capture(
        self,
        rtsp_url: Optional[str] = None,
        source_type: str | None = None,
        camera_index: int | None = None,
    ):
        source_type = source_type or self.source_type
        camera_index = self.camera_index if camera_index is None else camera_index
        if source_type == "video":
            self.active_source = "video"
            return cv2.VideoCapture(self.video_path)
        if source_type == "webcam":
            self.active_source = f"USB camera {camera_index}"
            camera_cap = _open_webcam_capture(camera_index)
            if camera_cap.isOpened():
                camera_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            return camera_cap

        # Prefer RTSP over TCP because Axis streams are often unreliable over UDP/NAT.
        os.environ.setdefault(
            "OPENCV_FFMPEG_CAPTURE_OPTIONS",
            "rtsp_transport;tcp",
        )
        acceleration = []
        if SETTINGS["decoder"] == "hardware":
            import platform
            supported = {"Windows": "VIDEO_ACCELERATION_D3D11", "Linux": "VIDEO_ACCELERATION_VAAPI"}
            flag = supported.get(platform.system())
            if not flag or not hasattr(cv2, flag):
                raise CameraUnavailableError("Hardware decoder unavailable. Check the OpenCV build or select software.")
            acceleration = [cv2.CAP_PROP_HW_ACCELERATION, getattr(cv2, flag)]
        rtsp_cap = cv2.VideoCapture(
            rtsp_url or self.rtsp_url,
            cv2.CAP_FFMPEG,
            [
                *acceleration,
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
                7000,
                cv2.CAP_PROP_READ_TIMEOUT_MSEC,
                7000,
            ],
        )
        if rtsp_cap.isOpened():
            rtsp_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.active_source = "rtsp"
            return rtsp_cap

        rtsp_cap.release()
        if not self.allow_webcam_fallback:
            self.active_source = "rtsp"
            return rtsp_cap

        webcam_cap = _open_webcam_capture(camera_index)
        if webcam_cap.isOpened():
            webcam_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.active_source = "webcam"
            print(f"RTSP source failed; switching to webcam index {camera_index}.")
            return webcam_cap

        webcam_cap.release()
        self.active_source = "rtsp"
        return webcam_cap

    def _draw_status(self, frame, status_text: str, text_color: tuple[int, int, int]) -> None:
        cv2.putText(
            frame,
            status_text,
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            text_color,
            2,
            cv2.LINE_AA,
        )

    @staticmethod
    def _crop_black_matte(frame):
        height, width = frame.shape[:2]
        if width < 20 or height < 20:
            return frame, 0, 0

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        _, active_pixels = cv2.threshold(gray, 24, 1, cv2.THRESH_BINARY)
        active_columns = cv2.reduce(active_pixels, 0, cv2.REDUCE_SUM, dtype=cv2.CV_32S).reshape(-1)
        columns_mask = (active_columns >= max(3, int(height * 0.005))).astype("uint8").reshape(1, -1)
        columns = cv2.findNonZero(columns_mask)
        if columns is None:
            return frame, 0, 0

        column_indices = columns.reshape(-1, 2)[:, 0]
        x1, x2 = int(column_indices[0]), int(column_indices[-1]) + 1
        border_width = max(1, int(width * 0.03))
        side_borders_are_dark = (
            cv2.mean(gray[:, :border_width])[0] <= 12
            and cv2.mean(gray[:, width - border_width :])[0] <= 12
        )
        if not side_borders_are_dark or (x1 == 0 and x2 == width):
            return frame, 0, 0

        padding_x = max(2, int((x2 - x1) * 0.02))
        x1 = max(0, x1 - padding_x)
        x2 = min(width, x2 + padding_x)
        if (x2 - x1) < width * 0.05:
            return frame, 0, 0

        return frame[:, x1:x2], x1, 0

    def _draw_detection_box(self, frame, box, label: str, color: tuple[int, int, int], offset_x: int, offset_y: int) -> None:
        if box is None:
            return

        adjusted_box = dict(box)
        adjusted_box["xyxy"] = [
            box["xyxy"][0] - offset_x,
            box["xyxy"][1] - offset_y,
            box["xyxy"][2] - offset_x,
            box["xyxy"][3] - offset_y,
        ]
        self.detector.draw_box(frame, adjusted_box, label, color)

    @staticmethod
    def _blackout_side_regions(frame, boxes, offset_x: int):
        if not boxes or boxes[0] is None:
            return frame

        visible_boxes = [box for box in boxes if box is not None]
        height, width = frame.shape[:2]
        x1 = min(box["xyxy"][0] for box in visible_boxes) - offset_x
        x2 = max(box["xyxy"][2] for box in visible_boxes) - offset_x
        object_width = max(box["xyxy"][2] - box["xyxy"][0] for box in visible_boxes)
        padding = max(2, int(object_width), int(width * 0.2))
        left = max(0, min(width, int(x1 - padding)))
        right = max(0, min(width, int(x2 + padding)))
        if left > 0:
            cv2.rectangle(frame, (0, 0), (left - 1, height - 1), (0, 0, 0), -1)
        if right < width:
            cv2.rectangle(frame, (right, 0), (width - 1, height - 1), (0, 0, 0), -1)
        return frame

    def _render_preview_frame(self, frame):
        display_frame, crop_x, crop_y = self._crop_black_matte(frame)
        display_frame = display_frame.copy()
        overlay = self._preview_overlay
        if overlay is not None:
            container_box, hook_box, lock_box, status_text, text_color = overlay
            self._blackout_side_regions(display_frame, (container_box, hook_box, lock_box), crop_x)
            self._draw_detection_box(display_frame, container_box, "cavity", BLUE, crop_x, crop_y)
            self._draw_detection_box(display_frame, hook_box, "hook", GREEN, crop_x, crop_y)
            self._draw_detection_box(display_frame, lock_box, "lock", YELLOW, crop_x, crop_y)
            if getattr(self, "show_window", False):
                self._draw_status(display_frame, status_text, text_color)

        height, width = display_frame.shape[:2]
        if width > PREVIEW_MAX_WIDTH:
            target_height = round(height * PREVIEW_MAX_WIDTH / width)
            display_frame = cv2.resize(
                display_frame,
                (PREVIEW_MAX_WIDTH, target_height),
                interpolation=cv2.INTER_AREA,
            )

        return display_frame

    def _publish_preview(self, frame):
        display_frame = self._render_preview_frame(frame)
        ok, encoded_frame = cv2.imencode(
            ".jpg",
            display_frame,
            [cv2.IMWRITE_JPEG_QUALITY, PREVIEW_JPEG_QUALITY],
        )
        if not ok:
            raise RuntimeError("Failed to encode the live camera preview.")
        self.latest_frame = display_frame
        self.latest_jpeg = encoded_frame.tobytes()
        self.jpeg_sequence = getattr(self, "jpeg_sequence", 0) + 1
        return display_frame

    def _start_detection_recording(self, frame) -> None:
        archive = getattr(self, "video_archive", None)
        if archive is None:
            return

        recording_path = None
        try:
            recording_path = archive.create_recording_path(getattr(self, "camera_id", None))
            height, width = frame.shape[:2]
            writer = cv2.VideoWriter(
                str(recording_path),
                cv2.VideoWriter_fourcc(*"mp4v"),
                PREVIEW_FPS,
                (width, height),
            )
            if not writer.isOpened():
                writer.release()
                archive.discard_recording(recording_path)
                LOGGER.error("Could not open video writer for detection recording %s.", recording_path)
                return

            self._recording_writer = writer
            self._recording_path = recording_path
            self._recording_last_frame_at = None
        except Exception:
            LOGGER.exception("Could not start detection recording for camera %s.", getattr(self, "camera_id", "camera"))
            if recording_path is not None:
                try:
                    archive.discard_recording(recording_path)
                except Exception:
                    LOGGER.exception("Could not discard incomplete detection recording %s.", recording_path)

    def _finish_detection_recording(self, discard: bool = False) -> None:
        writer = getattr(self, "_recording_writer", None)
        recording_path = getattr(self, "_recording_path", None)
        archive = getattr(self, "video_archive", None)
        self._recording_writer = None
        self._recording_path = None
        self._recording_last_frame_at = None

        if writer is not None:
            try:
                writer.release()
            except Exception:
                LOGGER.exception("Could not close detection recording for camera %s.", getattr(self, "camera_id", "camera"))
                discard = True

        if recording_path is None or archive is None:
            return
        try:
            if discard:
                archive.discard_recording(recording_path)
            else:
                completed_path = archive.complete_recording(recording_path)
                LOGGER.info("Saved detection recording %s.", completed_path)
        except Exception:
            LOGGER.exception("Could not finalize detection recording %s.", recording_path)

    def _record_detection_frame(self, frame, detected: bool) -> None:
        if getattr(self, "video_archive", None) is None:
            return
        if not detected:
            if getattr(self, "_recording_writer", None) is not None:
                self._finish_detection_recording()
            return

        if getattr(self, "_recording_writer", None) is None:
            self._start_detection_recording(frame)
        writer = getattr(self, "_recording_writer", None)
        if writer is None:
            return

        now = time.monotonic()
        last_frame_at = self._recording_last_frame_at
        frames_to_write = (
            1
            if last_frame_at is None
            else min(PREVIEW_FPS, int((now - last_frame_at) * PREVIEW_FPS))
        )
        if frames_to_write < 1:
            return
        try:
            for _ in range(frames_to_write):
                writer.write(frame)
            self._recording_last_frame_at = (
                now if last_frame_at is None else last_frame_at + frames_to_write / PREVIEW_FPS
            )
        except Exception:
            LOGGER.exception("Could not write detection frame for camera %s.", getattr(self, "camera_id", "camera"))
            self._finish_detection_recording(discard=True)

    def _maybe_speak_stable_location(
        self,
        container_box,
        hook_box,
        state_key: Optional[str] = None,
    ) -> None:
        if self.tts is None:
            self._tts_stable_since = None
            self._tts_location_state = None
            self._tts_location_last_alert = None
            return

        if container_box is None and hook_box is None:
            self._tts_lug_visible_since = None
            self._tts_stable_since = None
            self._tts_location_state = None
            self._tts_location_last_alert = None
            self._tts_hook_missing_alerted = False
            self.tts.cancel_pending(channel=getattr(self, "_tts_channel", id(self)))
            return

        if container_box is None:
            self._tts_lug_visible_since = None
            self._tts_stable_since = None
            self._tts_location_state = None
            self._tts_location_last_alert = None
            return

        now = time.monotonic()
        if self._tts_lug_visible_since is None:
            self._tts_lug_visible_since = now
        if hook_box is None:
            self._tts_stable_since = None
            self._tts_location_state = None
            self._tts_location_last_alert = None
            if now - self._tts_lug_visible_since < TTS_LUG_VISIBLE_DELAY_SECONDS:
                return
            lug_visible_messages = (LIFTING_LUG_VISIBLE_TEXT,) * TTS_LUG_VISIBLE_REPEAT_COUNT
            if not self._tts_hook_missing_alerted and self.tts.speak_sequence(
                *lug_visible_messages,
                channel=getattr(self, "_tts_channel", id(self)),
            ):
                self._tts_hook_missing_alerted = True
            return

        location_state = state_key
        if location_state is None:
            _, _, location_state = classify_hook_position(container_box, hook_box)
        if location_state not in {STATE_HOOK_INSIDE_LUG, STATE_HOOK_OUTSIDE_LUG}:
            self._tts_stable_since = None
            self._tts_location_state = None
            self._tts_location_last_alert = None
            return

        alert_message = STATUS_TEXT[location_state]
        if self._tts_location_state != location_state:
            self._tts_location_state = location_state
            self._tts_stable_since = now
            self._tts_location_last_alert = None
            return

        stable_for = now - self._tts_stable_since
        initial_alert_ready = (
            self._tts_location_last_alert is None
            and stable_for >= TTS_LOCATION_STABLE_DELAY_SECONDS
        )
        repeat_alert_ready = (
            self._tts_location_last_alert is not None
            and now - self._tts_location_last_alert >= TTS_LOCATION_ALERT_INTERVAL_SECONDS
        )
        if initial_alert_ready or repeat_alert_ready:
            alert_message = self._with_camera_side(alert_message)
            if self.tts.speak_sequence(
                *(alert_message,) * TTS_LUG_VISIBLE_REPEAT_COUNT,
                channel=getattr(self, "_tts_channel", id(self)),
            ):
                self._tts_location_last_alert = now
                self._tts_hook_missing_alerted = False

    def _with_camera_side(self, message: str) -> str:
        side_labels = {
            "left": "Lifting hook 1.",
            "right": "Lifting hook 2.",
        }
        camera_side = getattr(self, "camera_side", None)
        side_label = side_labels.get(camera_side) if isinstance(camera_side, str) else None
        return f"{side_label} {message}" if side_label else message

    @staticmethod
    def _box_gap(first_box, second_box) -> tuple[float, float, float]:
        first_x1, first_y1, first_x2, first_y2 = first_box["xyxy"]
        second_x1, second_y1, second_x2, second_y2 = second_box["xyxy"]
        horizontal_gap = max(0.0, first_x1 - second_x2, second_x1 - first_x2)
        vertical_gap = max(0.0, first_y1 - second_y2, second_y1 - first_y2)
        minimum_diagonal = min(
            hypot(first_x2 - first_x1, first_y2 - first_y1),
            hypot(second_x2 - second_x1, second_y2 - second_y1),
        )
        maximum_gap = minimum_diagonal * HOOK_LOCK_CONTACT_GAP_RATIO
        return hypot(horizontal_gap, vertical_gap), maximum_gap, minimum_diagonal

    @staticmethod
    def _hook_lock_engagement_percentages(hook_box, lock_box) -> tuple[int, int]:
        if hook_box is None and lock_box is None:
            return 0, 0
        if hook_box is None:
            return 0, 10
        if lock_box is None:
            return 10, 0

        gap, _, box_diagonal = HookStreamProcessor._box_gap(
            hook_box, lock_box
        )
        progress_distance = box_diagonal * HOOK_LOCK_PROGRESS_DISTANCE_RATIO
        progress = round(
            10 + 90 * (1.0 - min(1.0, gap / progress_distance))
        )
        engagement_percent = max(10, progress)
        return engagement_percent, engagement_percent

    @staticmethod
    def _smooth_engagement_percent(previous: int, target: int) -> int:
        if target == 0:
            return 0
        if previous == 0:
            previous = 10
        difference = target - previous
        if abs(difference) < 2:
            return target
        return max(10, min(100, round(previous + difference * 0.4)))

    @staticmethod
    def _boxes_touch_or_near(first_box, second_box) -> bool:
        if first_box is None or second_box is None:
            return False
        gap, maximum_gap, _ = HookStreamProcessor._box_gap(first_box, second_box)
        return gap <= maximum_gap

    def _update_hook_lock_contact(self, hook_box, lock_box) -> bool:
        return self._boxes_touch_or_near(hook_box, lock_box)

    def _smooth_lock_preview_box(self, lock_box):
        if lock_box is None:
            self._preview_lock_box = None
            return None

        previous_box = getattr(self, "_preview_lock_box", None)
        if previous_box is None:
            self._preview_lock_box = dict(lock_box)
            return self._preview_lock_box

        previous_coords = previous_box["xyxy"]
        current_coords = lock_box["xyxy"]
        intersection_width = max(
            0.0,
            min(previous_coords[2], current_coords[2])
            - max(previous_coords[0], current_coords[0]),
        )
        intersection_height = max(
            0.0,
            min(previous_coords[3], current_coords[3])
            - max(previous_coords[1], current_coords[1]),
        )
        intersection = intersection_width * intersection_height
        previous_area = max(0.0, previous_coords[2] - previous_coords[0]) * max(
            0.0, previous_coords[3] - previous_coords[1]
        )
        current_area = max(0.0, current_coords[2] - current_coords[0]) * max(
            0.0, current_coords[3] - current_coords[1]
        )
        union = previous_area + current_area - intersection
        if union == 0 or intersection / union < 0.25:
            self._preview_lock_box = dict(lock_box)
            return self._preview_lock_box

        smoothed_box = {
            **lock_box,
            "xyxy": [
                previous * 0.35 + current * 0.65
                for previous, current in zip(previous_coords, current_coords)
            ],
        }
        self._preview_lock_box = smoothed_box
        return smoothed_box

    def _speak_hook_lock_state(self, state_key: str) -> None:
        tts = getattr(self, "tts", None)
        if tts is None or state_key == getattr(self, "_tts_hook_lock_state", None):
            return
        channel = getattr(self, "_tts_channel", id(self))
        tts.cancel_pending(channel=channel)
        if tts.speak_sequence(
            self._with_camera_side(STATUS_TEXT[state_key]),
            channel=channel,
        ):
            self._tts_hook_lock_state = state_key

    def process_frame(self, frame, publish_preview: bool = True):
        detection_frame, crop_x, _ = self._crop_black_matte(frame)
        inference_started_at = time.monotonic()
        detections = self.detector.detect(detection_frame)
        inference_completed_at = time.monotonic()
        self._inference_latency_ms = round((inference_completed_at - inference_started_at) * 1000, 1)
        previous_inference_at = getattr(self, "_last_inference_at", None)
        if previous_inference_at is not None:
            interval = inference_started_at - previous_inference_at
            previous_average = getattr(self, "_inference_interval_s", None)
            self._inference_interval_s = interval if previous_average is None else previous_average * 0.8 + interval * 0.2
        self._last_inference_at = inference_started_at
        capture_at = getattr(self, "_capture_at", time.monotonic())
        if time.monotonic() - capture_at > SETTINGS["stale_seconds"]:
            self._mark_camera_unavailable("Detection delayed")
            self._preview_overlay = None
            return frame
        self._result_at = capture_at
        if crop_x:
            detections = {
                name: (
                    {
                        **box,
                        "xyxy": [
                            box["xyxy"][0] + crop_x,
                            box["xyxy"][1],
                            box["xyxy"][2] + crop_x,
                            box["xyxy"][3],
                        ],
                    }
                    if box is not None
                    else None
                )
                for name, box in detections.items()
            }
        container_box = detections.get("cavity") or detections.get(CONTAINER_CLASS)
        hook_box = detections.get("hook")
        lock_box = detections.get(LOCK_CLASS)
        preview_lock_box = self._smooth_lock_preview_box(lock_box)

        status_text, color, state_key = classify_hook_position(container_box, hook_box)
        hook_lock_contact = self._update_hook_lock_contact(hook_box, lock_box)
        target_hook_percent, target_lock_percent = (
            self._hook_lock_engagement_percentages(hook_box, lock_box)
        )
        if hook_box is not None and lock_box is not None:
            previous_pair_percent = getattr(self, "lock_engagement_percent", 0)
            pair_percent = self._smooth_engagement_percent(
                previous_pair_percent,
                target_lock_percent,
            )
            hook_engagement_percent = pair_percent
            lock_engagement_percent = pair_percent
        else:
            hook_engagement_percent = target_hook_percent
            lock_engagement_percent = target_lock_percent
        lift_ready = hook_lock_contact
        if hook_box is None:
            hook_lock_state = None
        elif lift_ready:
            hook_lock_state = STATE_HOOK_LOCK_ENGAGED
        else:
            hook_lock_state = STATE_HOOK_LOCK_NOT_ENGAGED
        observation = (state_key, lift_ready)
        if getattr(self, "event_index", None) and observation != getattr(self, "_previous_observation", None):
            import sqlite3
            try:
                stamp = time.time() - (time.monotonic() - capture_at)
                self.event_index.add(self.camera_id,stamp,state_key,lift_ready)
                self._previous_observation = observation
            except (OSError,sqlite3.Error):
                LOGGER.warning("Could not index detection for %s",self.camera_id)
        self._preview_overlay = (container_box, hook_box, preview_lock_box, status_text, color)
        self.last_status = status_text
        self.state = state_key.replace("_", " ").title()
        self.state_key = state_key
        location_inside = state_key == STATE_HOOK_INSIDE_LUG
        location_outside = state_key == STATE_HOOK_OUTSIDE_LUG
        self.severity = "Low" if lift_ready and location_inside else "High"
        self.hook = "Detected" if hook_box is not None else "Not engaged"
        self.inserted = "Yes" if location_inside else "No"
        self.lift = "Ready" if lift_ready else "Do Not Lift"
        self.offset = (
            "Inside"
            if location_inside
            else "Outside"
            if location_outside
            else "-"
        )
        self.meter = lock_engagement_percent
        self.updated = "Just now"
        self.alert_main = status_text
        self.alert_state = state_key.replace("_", " ").title()
        self.hook_engaged = lift_ready
        self.hook_lock_contact = hook_lock_contact
        self.hook_engagement_percent = hook_engagement_percent
        self.lock_engagement_percent = lock_engagement_percent
        self.hook_lock_state = hook_lock_state
        if hook_lock_state is None:
            self.hook_lock_command = ""
            self.hook_lock_command_hi = ""
        else:
            self.hook_lock_command = HOOK_LOCK_COMMAND_TEXT[hook_lock_state]
            self.hook_lock_command_hi = STATUS_TEXT[hook_lock_state]
        self.lift_ready = lift_ready
        self.cavity_detected = container_box is not None
        self.hook_detected = hook_box is not None
        self.lock_detected = lock_box is not None
        self.alert_message_hi = STATUS_TEXT[state_key]
        self.camera_connected = True
        self.camera_error = None
        self.retrying = False

        if hook_box is None and not lift_ready:
            self._tts_hook_lock_state = None
            self._maybe_speak_stable_location(
                container_box, hook_box, state_key
            )
        elif hook_lock_state is not None:
            self._speak_hook_lock_state(hook_lock_state)

        rendered_frame = self._publish_preview(frame) if publish_preview else self._render_preview_frame(frame)
        self._record_detection_frame(
            rendered_frame,
            detected=container_box is not None or hook_box is not None or lock_box is not None,
        )
        return rendered_frame

    def run(self) -> None:
        if self.stop_event.is_set():
            return

        if not hasattr(self, "_camera_settings_lock"):
            self._camera_settings_lock = threading.Lock()
            self._camera_generation = 0

        self.latest_frame = None
        with self._camera_settings_lock:
            camera_generation = self._camera_generation
            rtsp_url = getattr(self, "rtsp_url", None) or os.getenv("RTSP_URL", DEFAULT_RTSP_URL)
            source_type = self.source_type
            camera_index = getattr(self, "camera_index", 0)
        cap = self._resolve_capture(rtsp_url, source_type, camera_index)
        with self._camera_settings_lock:
            configuration_changed = camera_generation != self._camera_generation
        if configuration_changed:
            cap.release()
            raise CameraUnavailableError("Camera settings changed; reconnecting.")
        if not cap.isOpened():
            cap.release()
            with self._camera_settings_lock:
                configuration_changed = camera_generation != self._camera_generation
            if configuration_changed:
                raise CameraUnavailableError("Camera settings changed; reconnecting.")
            if source_type == "rtsp":
                source = "Axis RTSP/TCP stream"
                message = f"Unable to open {source}. Check RTSP access, credentials, and source settings."
            elif source_type == "webcam":
                source = self.active_source
                message = f"Unable to open {source}. Check that it is connected and try another USB device index."
            else:
                source = self.active_source
                message = f"Unable to open {source}. Check the selected source settings."
            self._mark_camera_unavailable(message)
            raise CameraUnavailableError(message)

        self.camera_connected = False
        self.camera_error = None
        self.retrying = False

        if self.source_type == "video":
            try:
                while not self.stop_event.is_set():
                    ret, frame = cap.read()
                    if not ret:
                        self.last_status = "Stream ended"
                        self.camera_connected = False
                        self.latest_frame = None
                        break

                    processed_frame = self.process_frame(frame)
                    if self.show_window:
                        cv2.imshow("NMDC Lifting Hook Detector", processed_frame)
                        if cv2.waitKey(1) & 0xFF == ord("q"):
                            break
            finally:
                self._finish_detection_recording()
                cap.release()
                if self.show_window:
                    cv2.destroyAllWindows()
            return

        frame_lock = threading.Lock()
        capture_lock = threading.Lock()
        stop_capture = threading.Event()
        captured = {"frame": None, "sequence": 0, "stopped": False, "error": None}

        def capture_latest_frame() -> None:
            while not stop_capture.is_set():
                try:
                    with capture_lock:
                        if stop_capture.is_set():
                            return
                        ret, frame = cap.read()
                except cv2.error as error:
                    with frame_lock:
                        captured["error"] = str(error)
                        captured["stopped"] = True
                    return
                if not ret:
                    with frame_lock:
                        captured["stopped"] = True
                    return
                with frame_lock:
                    captured["timestamp"] = time.monotonic()
                    captured["frame"] = frame
                    captured["sequence"] += 1

        capture_thread = threading.Thread(target=capture_latest_frame, daemon=True)
        capture_thread.start()
        preview_thread_stop = threading.Event()

        def publish_latest_frame() -> None:
            published_sequence = 0
            preview_interval = 1.0 / PREVIEW_FPS
            last_published_at = 0.0
            while not preview_thread_stop.is_set():
                with frame_lock:
                    frame = captured["frame"]
                    sequence = captured["sequence"]
                now = time.monotonic()
                if frame is not None and sequence != published_sequence and now - last_published_at >= preview_interval:
                    self._publish_preview(frame)
                    published_sequence = sequence
                    last_published_at = now
                preview_thread_stop.wait(0.01)

        preview_thread = threading.Thread(target=publish_latest_frame, daemon=True)
        preview_thread.start()
        processed_sequence = 0
        capture_stopped = False
        try:
            while not self.stop_event.is_set():
                with frame_lock:
                    frame = captured["frame"]
                    sequence = captured["sequence"]
                    capture_stopped = captured["stopped"]
                    capture_error = captured["error"]
                with self._camera_settings_lock:
                    configuration_changed = camera_generation != self._camera_generation
                if configuration_changed:
                    break

                if sequence == processed_sequence:
                    if capture_stopped:
                        message = (
                            f"Stream error: {capture_error}"
                            if capture_error
                            else "Camera stream ended or stopped delivering frames."
                        )
                        self._mark_camera_unavailable(message)
                        break
                    time.sleep(0.005)
                    continue

                with frame_lock:
                    frame = captured["frame"]
                    sequence = captured["sequence"]
                    self._capture_at = captured["timestamp"]
                if time.monotonic() < getattr(self,"_next_inference",0):
                    self.stop_event.wait(0.01)
                    continue
                self._next_inference = time.monotonic() + 1/SETTINGS["analysis_fps"]
                processed_sequence = sequence
                processed_frame = self.process_frame(frame, publish_preview=False)

                if self.show_window:
                    cv2.imshow("NMDC Lifting Hook Detector", processed_frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
        finally:
            stop_capture.set()
            preview_thread_stop.set()
            with capture_lock:
                cap.release()
            capture_thread.join(timeout=1.0)
            preview_thread.join(timeout=1.0)
            self._finish_detection_recording()
            if self.show_window:
                cv2.destroyAllWindows()

        with self._camera_settings_lock:
            configuration_changed = camera_generation != self._camera_generation
        if configuration_changed:
            raise CameraUnavailableError("Camera settings changed; reconnecting.")
        if capture_stopped and not self.stop_event.is_set():
            raise CameraUnavailableError(self.camera_error or "Camera stream stopped.")

    def request_stop(self) -> None:
        self.stop_event.set()

    def stop(self) -> None:
        self.request_stop()
        self.camera_connected = False
        if self.tts is not None and getattr(self, "_owns_tts", True):
            self.tts.stop()
