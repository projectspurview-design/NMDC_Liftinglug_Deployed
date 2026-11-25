from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from .camera_admin import DEFAULT_CAMERA_ID, CameraAdminStore
from .event_index import Events
from .stream import CameraUnavailableError, HookStreamProcessor

LOGGER = logging.getLogger(__name__)


class CameraFeedManager:
    def __init__(
        self,
        primary_processor: HookStreamProcessor,
        camera_admin: CameraAdminStore,
        processor_factory: Callable[[str], HookStreamProcessor] | None = None,
    ) -> None:
        self.primary_processor = primary_processor
        self.primary_processor._owns_tts = False
        self.camera_admin = camera_admin
        self._processor_factory = processor_factory or self._create_processor
        self._processors: dict[str, HookStreamProcessor] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._camera_stop_events: dict[str, threading.Event] = {}
        self._primary_processor_retired = False
        self.event_index: Events | None = None
        self._lock = threading.RLock()
        self._stop_event = threading.Event()

    def _create_processor(self, camera_id: str) -> HookStreamProcessor:
        return HookStreamProcessor(
            model_path=self.primary_processor.model_path,
            enable_tts=False,
            conf=self.primary_processor.detector.confidence,
            show_window=False,
            detector=self.primary_processor.detector,
            camera_side=self._camera_side(camera_id),
            tts_manager=self.primary_processor.tts,
            video_archive=getattr(self.primary_processor, "video_archive", None),
            camera_id=camera_id,
        )

    @staticmethod
    def _camera_side(camera_id: str) -> str | None:
        camera_number = camera_id.rsplit("-Camera-", 1)[-1]
        return {"1": "left", "2": "right"}.get(camera_number)

    def configure_camera(self, camera_id: str, configuration: dict[str, Any]) -> None:
        with self._lock:
            if self._stop_event.is_set():
                raise RuntimeError("Camera feeds are stopping and cannot be configured.")
            if configuration["source_type"] == "usb":
                source_type = "webcam"
                camera_index = configuration["device_index"]
                rtsp_url = None
            else:
                source_type = "rtsp"
                camera_index = 0
                rtsp_url = self.camera_admin.camera_rtsp_url(camera_id)
            from .deployment import SETTINGS
            from urllib.parse import urlsplit, urlunsplit
            profile = SETTINGS["camera_profiles"].get(camera_id, {})
            archive = getattr(self, "archive", None)
            if rtsp_url:
                base = urlsplit(rtsp_url)
                def stream_url(key):
                    path = profile.get(key) or configuration.get("rtsp_path") or SETTINGS[key]
                    if not path.startswith("/") or path.startswith("//"):
                        raise ValueError("Camera profile must be a path on the configured camera")
                    return urlunsplit((base.scheme,base.netloc,path,"",""))
                if archive:
                    archive.configure(camera_id, stream_url("main_path"))
                rtsp_url = stream_url("analysis_path")
            elif archive:
                archive.disconnect(camera_id)
            processor = self._processors.get(camera_id)
            if processor is None:
                processor = (
                    self.primary_processor
                    if camera_id == DEFAULT_CAMERA_ID and not self._primary_processor_retired
                    else self._processor_factory(camera_id)
                )
                processor.show_window = False
                processor.allow_webcam_fallback = False
                processor.camera_side = self._camera_side(camera_id)
                processor.camera_id = camera_id
                processor.video_archive = getattr(self.primary_processor, "video_archive", None)
                self._processors[camera_id] = processor
            processor.event_index = self.event_index or (archive.events if archive else None)
            processor.set_camera_source(source_type, camera_index, rtsp_url)

            if camera_id not in self._threads:
                camera_stop_event = threading.Event()
                self._camera_stop_events[camera_id] = camera_stop_event
                thread = threading.Thread(
                    target=self._run_camera,
                    args=(camera_id, processor, camera_stop_event),
                    name=f"camera-{camera_id}",
                    daemon=True,
                )
                self._threads[camera_id] = thread
                thread.start()

    def _run_camera(
        self,
        camera_id: str,
        processor: HookStreamProcessor,
        camera_stop_event: threading.Event,
    ) -> None:
        while not self._stop_event.is_set() and not camera_stop_event.is_set():
            try:
                processor.run()
            except CameraUnavailableError as error:
                if self._stop_event.is_set() or camera_stop_event.is_set():
                    return
                processor.retrying = True
                processor.last_status = str(error)
                processor.alert_main = str(error)
                LOGGER.warning("Camera %s unavailable: %s", camera_id, error)
                delay = 0.1 if "settings changed" in str(error).lower() else 5
            except Exception:
                if self._stop_event.is_set() or camera_stop_event.is_set():
                    return
                LOGGER.exception("Unexpected error while processing camera %s.", camera_id)
                processor.retrying = True
                processor.camera_error = "Unexpected camera processing error. Check the detector logs."
                processor.last_status = processor.camera_error
                processor.alert_main = processor.camera_error
                delay = 5
            else:
                delay = 0.5
            camera_stop_event.wait(delay)

    def disconnect_camera(self, camera_id: str) -> None:
        camera_id = self.camera_admin._validate_camera_id(camera_id)
        with self._lock:
            processor = self._processors.get(camera_id)
            thread = self._threads.get(camera_id)
            stop_event = self._camera_stop_events.get(camera_id)
            if processor is None:
                return
            if getattr(self, "archive", None):
                self.archive.disconnect(camera_id)
            if stop_event is not None:
                stop_event.set()
            processor.stop()
            if thread is not None:
                thread.join(timeout=10)
                if thread.is_alive():
                    raise RuntimeError(f"Camera {camera_id} did not stop. Check for an RTSP read timeout.")
            processor._mark_camera_unavailable(
                "Camera settings were reset. Enter credentials to reconnect."
            )
            if processor is self.primary_processor:
                self._primary_processor_retired = True
            self._processors.pop(camera_id, None)
            self._threads.pop(camera_id, None)
            self._camera_stop_events.pop(camera_id, None)

    def get_processor(self, camera_id: str) -> HookStreamProcessor | None:
        with self._lock:
            return self._processors.get(camera_id)

    def get_status(self, camera_id: str) -> dict[str, object]:
        processor = self.get_processor(camera_id)
        if processor is None:
            return {
                "status": "Camera not configured.",
                "frame_ready": False,
                "camera_connected": False,
                "camera_source": "rtsp",
                "camera_error": "Save this camera's connection settings to start its feed.",
                "retrying": False,
                "state": "-",
                "severity": "-",
                "hook": "-",
                "inserted": "-",
                "lift": "-",
                "offset": "-",
                "updated": "-",
                "alert_main": "Camera not configured.",
                "alert_state": "-",
                "meter": 0,
                "state_key": "-",
                "hook_engaged": False,
                "hook_lock_contact": False,
                "hook_engagement_percent": 0,
                "lock_engagement_percent": 0,
                "lift_ready": False,
                "cavity_detected": False,
                "hook_detected": False,
                "lock_detected": False,
                "alert_message_hi": "",
            }
        result = processor.status_snapshot()
        archive = getattr(self, "archive", None)
        writer = archive.recorders.get(camera_id) if archive else None
        result["recording_state"] = writer.state if writer else "event clips or inactive"
        result["last_segment_at"] = writer.last_segment if writer else None
        return result

    def get_health(self, camera_id: str) -> dict[str, object]:
        processor = self.get_processor(camera_id)
        if processor is None:
            return {
                "ok": True,
                "camera_connected": False,
                "frame_ready": False,
                "camera_source": "rtsp",
                "camera_error": "Camera is not configured.",
                "retrying": False,
            }
        return processor.health_snapshot()

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            processors = tuple(self._processors.values())
            threads = tuple(self._threads.values())
            stop_events = tuple(self._camera_stop_events.values())
        for stop_event in stop_events:
            stop_event.set()
        if self.primary_processor not in processors:
            processors += (self.primary_processor,)
        for processor in processors:
            processor.stop()
        tts_manager = getattr(self.primary_processor, "tts", None)
        if tts_manager is not None:
            tts_manager.stop()
        for thread in threads:
            thread.join(timeout=8)
            if thread.is_alive():
                LOGGER.warning("Camera worker %s did not stop before timeout.", thread.name)

    def request_stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            processors = tuple(self._processors.values())
        for processor in processors:
            processor.request_stop()
        self.primary_processor.request_stop()
