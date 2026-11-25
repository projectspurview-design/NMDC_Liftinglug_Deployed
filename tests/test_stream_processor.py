import unittest
import os
import tempfile
import threading
import wave
from collections import deque
from queue import Queue
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import cv2
import numpy as np

from hook_detector.classifier import classify_hook_position
from hook_detector.auth import AuthStore, SESSION_COOKIE_NAME
from hook_detector.config import (
    STATE_GAP_MISSING,
    STATE_HOOK_INSIDE_LUG,
    STATE_HOOK_NOT_ENGAGED,
    STATE_HOOK_LOCK_ENGAGED,
    STATE_HOOK_LOCK_NOT_ENGAGED,
    STATE_HOOK_LOCK_CONTACT_NOT_READY,
    STATE_HOOK_OUTSIDE_LUG,
    LIFTING_LUG_VISIBLE_TEXT,
    DISPLAY_STATUS_TEXT,
    STATUS_TEXT,
    _axis_rtsp_url,
)
from hook_detector.detector import HookYOLODetector, _prepare_torchvision_nms
from hook_detector.deepx_backend import DeepXDetector
from hook_detector.stream import CameraUnavailableError, HookStreamProcessor
from hook_detector.tts import TTSManager
from hook_detector.web_ui import start_backend_server


def _start_authenticated_backend(processor):
    auth_directory = tempfile.TemporaryDirectory()
    auth_store = AuthStore(auth_directory.name, cookie_secure=False)
    auth_store.create_initial_admin("test-admin", "test-admin-password")
    _, session_token, csrf_token = auth_store.login("test-admin", "test-admin-password")
    server = start_backend_server(
        processor,
        host="127.0.0.1",
        port=0,
        auth_store=auth_store,
    )
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={session_token}",
        "X-CSRF-Token": csrf_token,
    }
    return server, auth_directory, headers


class HookStreamProcessorFrontendTest(unittest.TestCase):
    def test_deepx_keeps_independent_overlapping_class_scores(self):
        rows = np.zeros((6, 100), dtype=np.float32)
        rows[:4, 0] = [320, 320, 100, 100]
        rows[4:, 0] = [0.8, 0.7]

        class Engine:
            def run(self, tensor):
                return [rows[np.newaxis, ...]]

        detector = DeepXDetector.__new__(DeepXDetector)
        detector.engines = [Engine()]
        detector.confidence = 0.25
        detector.waiters = deque()
        detector.condition = threading.Condition()

        detections = detector.detect(np.zeros((640, 640, 3), dtype=np.uint8))

        self.assertAlmostEqual(detections["hook"]["confidence"], 0.8)
        self.assertAlmostEqual(detections["container"]["confidence"], 0.7)

    def test_axis_environment_builds_escaped_rtsp_url(self):
        axis_environment = {
            "AXIS_CAMERA_IP": "192.168.1.20",
            "AXIS_CAMERA_USERNAME": "camera user",
            "AXIS_CAMERA_PASSWORD": "p@ss/word",
        }

        with patch.dict(os.environ, axis_environment, clear=True):
            self.assertEqual(
                _axis_rtsp_url(),
                "rtsp://camera%20user:p%40ss%2Fword@192.168.1.20/axis-media/media.amp?streamprofile=Profile_1",
            )

    def test_frontend_state_fields_exist(self):
        detector = SimpleNamespace(model_paths=[
            "weights/nmdc-yolo.dxnn",
            "weights/nmdchook.dxnn",
            "weights/exp.dxnn",
            "weights/new_lock 1.dxnn",
        ])
        processor = HookStreamProcessor(
            model_path="weights/nmdc-yolo.pt",
            detector=detector,
            enable_tts=False,
            show_window=False,
        )

        self.assertEqual(processor.last_status, "Initializing")
        self.assertIsNone(processor.latest_frame)
        status = processor.status_snapshot()
        self.assertEqual(status["inference_backend"], "DXNN")
        self.assertEqual(status["model_files"], [
            "nmdc-yolo.dxnn",
            "nmdchook.dxnn",
            "exp.dxnn",
            "new_lock 1.dxnn",
        ])
        self.assertIn("ai_box_cpu_percent", status)
        self.assertIn("npu_utilization_percent", status)
        self.assertIn("npu_devices", status)
        self.assertIn("inference_latency_ms", status)
        self.assertIn("inference_fps", status)
        self.assertEqual(status["meter"], 0)
        detector.model_paths = ["weights/yolo.pt"]
        self.assertEqual(processor.status_snapshot()["inference_backend"], "PT")
        self.assertEqual(processor.status_snapshot()["model_files"], ["yolo.pt"])

    def test_capture_error_stops_stream_and_updates_status(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        processor.stop_event = threading.Event()
        processor.source_type = "rtsp"
        processor.active_source = "rtsp"
        processor.show_window = False
        processor.last_status = "Initializing"
        processor.latest_frame = None
        processor.camera_connected = True
        processor.camera_error = None
        processor.retrying = False

        cap = unittest.mock.Mock()
        cap.isOpened.return_value = True
        cap.read.side_effect = cv2.error("Unknown C++ exception from OpenCV code")

        with patch.object(processor, "_resolve_capture", return_value=cap):
            with self.assertRaises(CameraUnavailableError):
                processor.run()

        self.assertEqual(
            processor.last_status,
            "Stream error: Unknown C++ exception from OpenCV code",
        )
        self.assertFalse(processor.camera_connected)
        self.assertEqual(processor.camera_error, processor.last_status)
        self.assertTrue(processor.health_snapshot()["ok"])
        self.assertFalse(processor.health_snapshot()["camera_connected"])
        self.assertEqual(processor.health_snapshot()["camera_error"], processor.last_status)
        cap.release.assert_called_once_with()

    def test_camera_open_failure_is_a_retryable_error(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        processor.stop_event = threading.Event()
        processor.source_type = "rtsp"
        processor.active_source = "rtsp"
        processor.camera_connected = False
        processor.camera_error = None
        processor.last_status = "Initializing"
        processor.latest_frame = None

        cap = unittest.mock.Mock()
        cap.isOpened.return_value = False

        with patch.object(processor, "_resolve_capture", return_value=cap):
            with self.assertRaises(CameraUnavailableError):
                processor.run()

        self.assertIn("Axis RTSP/TCP stream", processor.camera_error)
        self.assertIn("RTSP access", processor.camera_error)
        cap.release.assert_called_once_with()

    def test_rtsp_capture_uses_ffmpeg_tcp_with_timeouts(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        processor.source_type = "rtsp"
        processor.rtsp_url = "rtsp://camera/axis-media/media.amp"
        processor.camera_index = 0

        cap = unittest.mock.Mock()
        cap.isOpened.return_value = True

        with patch.dict(os.environ, {}, clear=True):
            with patch("hook_detector.stream.cv2.VideoCapture", return_value=cap) as open_capture:
                self.assertIs(processor._resolve_capture(), cap)
            self.assertEqual(
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"],
                "rtsp_transport;tcp",
            )

        open_capture.assert_called_once_with(
            processor.rtsp_url,
            cv2.CAP_FFMPEG,
            [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
                7000,
                cv2.CAP_PROP_READ_TIMEOUT_MSEC,
                7000,
            ],
        )
        cap.set.assert_called_once_with(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.assertEqual(processor.active_source, "rtsp")

    def test_camera_is_connected_only_after_a_frame_is_processed(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        processor.detector = unittest.mock.Mock()
        processor.detector.detect.return_value = {"container": None, "hook": None}
        processor.active_source = "rtsp"
        processor.latest_frame = None
        processor.camera_connected = False
        processor.camera_error = "Waiting for camera frame"
        processor.retrying = True

        with patch.object(processor, "_draw_status"), patch.object(processor, "_maybe_speak_stable_location"):
            processor.process_frame(np.zeros((32, 32, 3), dtype=np.uint8))

        self.assertTrue(processor.camera_connected)
        self.assertIsNone(processor.camera_error)
        self.assertFalse(processor.retrying)
        status = processor.status_snapshot()
        self.assertTrue(status["frame_ready"])
        self.assertIsNotNone(status["inference_latency_ms"])

        with patch.object(processor, "_draw_status"), patch.object(processor, "_maybe_speak_stable_location"):
            processor.process_frame(np.zeros((32, 32, 3), dtype=np.uint8))

        self.assertGreater(processor.status_snapshot()["inference_fps"], 0)

    def test_preview_and_detector_crop_black_side_matte(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        processor.detector = unittest.mock.Mock()
        processor.detector.detect.return_value = {"container": None, "hook": None}
        processor.active_source = "rtsp"
        processor.latest_frame = None
        processor.camera_connected = False
        processor.camera_error = None
        processor.retrying = False
        source_frame = np.zeros((100, 200, 3), dtype=np.uint8)
        source_frame[10:90, 70:130] = 120

        with patch.object(processor, "_draw_status"), patch.object(processor, "_maybe_speak_stable_location"):
            preview_frame = processor.process_frame(source_frame)

        self.assertEqual(processor.detector.detect.call_args.args[0].shape, (100, 64, 3))
        self.assertEqual(preview_frame.shape, (100, 64, 3))
        self.assertEqual(processor.latest_frame.shape, (100, 64, 3))
        self.assertTrue(processor.latest_jpeg.startswith(b"\xff\xd8"))
        self.assertEqual(processor.jpeg_sequence, 1)
        self.assertEqual(source_frame.shape, (100, 200, 3))

    def test_process_frame_maps_cropped_detection_boxes_to_preview(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        processor.detector = unittest.mock.Mock()
        processor.detector.detect.return_value = {
            "container": {"xyxy": [10, 20, 50, 80], "confidence": 0.9},
            "hook": {"xyxy": [20, 30, 30, 40], "confidence": 0.8},
        }
        processor.active_source = "rtsp"
        processor.latest_frame = None
        processor.camera_connected = False
        processor.camera_error = None
        processor.retrying = False
        source_frame = np.zeros((100, 200, 3), dtype=np.uint8)
        source_frame[10:90, 70:130] = 120

        with patch.object(processor, "_draw_status"), patch.object(processor, "_maybe_speak_stable_location"):
            processor.process_frame(source_frame)

        self.assertEqual(processor._preview_overlay[0]["xyxy"], [78, 20, 118, 80])
        self.assertEqual(processor._preview_overlay[1]["xyxy"], [88, 30, 98, 40])
        lug_draw_box = processor.detector.draw_box.call_args_list[0].args[1]
        hook_draw_box = processor.detector.draw_box.call_args_list[1].args[1]
        self.assertEqual(lug_draw_box["xyxy"], [10, 20, 50, 80])
        self.assertEqual(hook_draw_box["xyxy"], [20, 30, 30, 40])

    def test_preview_blacks_out_sides_outside_detected_objects(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        processor.detector = unittest.mock.Mock()
        processor.detector.detect.return_value = {
            "container": {"xyxy": [80, 20, 110, 80], "confidence": 0.9},
            "hook": {"xyxy": [88, 30, 98, 40], "confidence": 0.8},
        }
        processor.active_source = "rtsp"
        processor.latest_frame = None
        processor.camera_connected = False
        processor.camera_error = None
        processor.retrying = False
        source_frame = np.full((100, 200, 3), 120, dtype=np.uint8)

        with patch.object(processor, "_draw_status"), patch.object(processor, "_maybe_speak_stable_location"):
            preview_frame = processor.process_frame(source_frame)

        self.assertTrue(np.all(preview_frame[:, :40] == 0))
        self.assertTrue(np.all(preview_frame[:, 151:] == 0))
        self.assertTrue(np.all(preview_frame[:, 70:120] == 120))

    def test_preview_keeps_full_frame_when_lug_or_hook_is_missing(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        processor.detector = unittest.mock.Mock()
        processor.detector.detect.return_value = {
            "container": None,
            "hook": {"xyxy": [88, 30, 98, 40], "confidence": 0.8},
        }
        processor.active_source = "rtsp"
        processor.latest_frame = None
        processor.camera_connected = False
        processor.camera_error = None
        processor.retrying = False
        source_frame = np.full((100, 200, 3), 120, dtype=np.uint8)

        with patch.object(processor, "_draw_status"), patch.object(processor, "_maybe_speak_stable_location"):
            preview_frame = processor.process_frame(source_frame)

        self.assertTrue(np.all(preview_frame[:, :50] == 120))
        self.assertTrue(np.all(preview_frame[:, 141:] == 120))
        self.assertEqual(processor._preview_overlay[0], None)
        self.assertIsNotNone(processor._preview_overlay[1])

    def test_preview_blacks_out_sides_when_lug_is_visible_without_hook(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        processor.detector = unittest.mock.Mock()
        processor.detector.detect.return_value = {
            "container": {"xyxy": [80, 20, 110, 80], "confidence": 0.9},
            "hook": None,
        }
        processor.active_source = "rtsp"
        processor.latest_frame = None
        processor.camera_connected = False
        processor.camera_error = None
        processor.retrying = False
        source_frame = np.full((100, 200, 3), 120, dtype=np.uint8)

        with patch.object(processor, "_draw_status"), patch.object(processor, "_maybe_speak_stable_location"):
            preview_frame = processor.process_frame(source_frame)

        self.assertTrue(np.all(preview_frame[:, :40] == 0))
        self.assertTrue(np.all(preview_frame[:, 151:] == 0))
        self.assertTrue(np.all(preview_frame[:, 70:120] == 120))
        self.assertIsNotNone(processor._preview_overlay[0])
        self.assertIsNone(processor._preview_overlay[1])

    def test_preview_blackout_keeps_a_wide_margin_around_detected_objects(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        processor.detector = unittest.mock.Mock()
        processor.detector.detect.return_value = {
            "container": {"xyxy": [90, 20, 100, 80], "confidence": 0.9},
            "hook": {"xyxy": [92, 30, 98, 40], "confidence": 0.8},
        }
        processor.active_source = "rtsp"
        processor.latest_frame = None
        processor.camera_connected = False
        processor.camera_error = None
        processor.retrying = False
        source_frame = np.full((100, 200, 3), 120, dtype=np.uint8)

        with patch.object(processor, "_draw_status"), patch.object(processor, "_maybe_speak_stable_location"):
            preview_frame = processor.process_frame(source_frame)

        self.assertTrue(np.all(preview_frame[:, :50] == 0))
        self.assertTrue(np.all(preview_frame[:, 60:130] == 120))
        self.assertTrue(np.all(preview_frame[:, 141:] == 0))

    def test_stream_preview_publication_does_not_wait_for_inference(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        processor.detector = unittest.mock.Mock()
        processor.detector.detect.return_value = {"container": None, "hook": None}
        processor.active_source = "rtsp"
        processor.latest_frame = None
        processor.latest_jpeg = None
        processor.camera_connected = False
        processor.camera_error = None
        processor.retrying = False

        with patch.object(processor, "_draw_status"), patch.object(processor, "_maybe_speak_stable_location"):
            with patch.object(processor, "_publish_preview", side_effect=AssertionError("inference attempted to publish")):
                processor.process_frame(np.zeros((32, 32, 3), dtype=np.uint8), publish_preview=False)

        self.assertIsNone(processor.latest_jpeg)
        self.assertIsNotNone(processor._preview_overlay)

    def test_stream_preview_preserves_full_hd_resolution(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        processor.detector = unittest.mock.Mock()
        processor.latest_frame = None
        processor.latest_jpeg = None
        processor.jpeg_sequence = 0
        processor._preview_overlay = None
        frame = np.full((1080, 1920, 3), (40, 100, 180), dtype=np.uint8)

        preview = processor._publish_preview(frame)

        self.assertEqual(preview.shape, (1080, 1920, 3))
        decoded = cv2.imdecode(np.frombuffer(processor.latest_jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(decoded.shape, (1080, 1920, 3))
        self.assertEqual(processor.jpeg_sequence, 1)

    def test_snapshot_endpoint_reuses_cached_jpeg(self):
        processor = unittest.mock.Mock()
        processor.latest_frame = np.zeros((24, 32, 3), dtype=np.uint8)
        processor.latest_jpeg = b"cached-jpeg"
        server, auth_directory, auth_headers = _start_authenticated_backend(processor)
        url = f"http://127.0.0.1:{server.server_port}/snapshot.jpg"

        try:
            with patch("hook_detector.web_ui.cv2.imencode", side_effect=AssertionError("frame was re-encoded")):
                with urlopen(Request(url, headers=auth_headers), timeout=2) as response:
                    self.assertEqual(response.read(), b"cached-jpeg")
                    self.assertEqual(response.headers.get_content_type(), "image/jpeg")
        finally:
            server.shutdown()
            server.server_close()
            auth_directory.cleanup()

    def test_backend_root_serves_detection_monitor_without_exposing_status(self):
        processor = unittest.mock.Mock()
        server, auth_directory, _ = _start_authenticated_backend(processor)
        url = f"http://127.0.0.1:{server.server_port}/"

        try:
            with urlopen(url, timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get_content_type(), "text/html")
                self.assertIn(b"NMDC Detection Monitor", response.read())
            with self.assertRaises(HTTPError) as error:
                urlopen(f"http://127.0.0.1:{server.server_port}/status", timeout=2)
            self.assertEqual(error.exception.code, 401)
        finally:
            server.shutdown()
            server.server_close()
            auth_directory.cleanup()

    def test_snapshot_endpoint_serves_current_jpeg_and_reports_missing_frame(self):
        processor = unittest.mock.Mock()
        processor.latest_frame = np.zeros((24, 32, 3), dtype=np.uint8)
        processor.latest_jpeg = None
        server, auth_directory, auth_headers = _start_authenticated_backend(processor)
        url = f"http://127.0.0.1:{server.server_port}/snapshot.jpg"

        try:
            with urlopen(Request(url, headers=auth_headers), timeout=2) as response:
                image = response.read()
                self.assertEqual(response.headers.get_content_type(), "image/jpeg")
                self.assertEqual(response.status, 200)
                self.assertTrue(image.startswith(b"\xff\xd8"))
                self.assertTrue(image.endswith(b"\xff\xd9"))

            processor.latest_frame = None
            processor.latest_jpeg = None
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(url, headers=auth_headers), timeout=2)
            self.assertEqual(error.exception.code, 503)
        finally:
            server.shutdown()
            server.server_close()
            auth_directory.cleanup()

    def test_snapshot_endpoint_serves_newest_frame_on_repeated_requests(self):
        processor = unittest.mock.Mock()
        processor.latest_frame = np.zeros((24, 32, 3), dtype=np.uint8)
        processor.latest_jpeg = None
        server, auth_directory, auth_headers = _start_authenticated_backend(processor)
        url = f"http://127.0.0.1:{server.server_port}/snapshot.jpg"

        try:
            with urlopen(Request(url, headers=auth_headers), timeout=2) as first_response:
                first_image = first_response.read()

            processor.latest_frame = np.full((24, 32, 3), 255, dtype=np.uint8)
            with urlopen(Request(url, headers=auth_headers), timeout=2) as second_response:
                second_image = second_response.read()

            self.assertNotEqual(first_image, second_image)
        finally:
            server.shutdown()
            server.server_close()
            auth_directory.cleanup()

    def test_lifting_lug_and_lifting_hook_class_aliases_remain_distinct(self):
        for lug_name in ("Cavity", "container", "lug", "lifting lug", "lifting_lug", "lifting-lug"):
            with self.subTest(name=lug_name):
                self.assertEqual(HookYOLODetector._normalize_class_name(lug_name), "container")

        for hook_name in ("hook", "lifting hook", "lifting_hook", "lifting-hook"):
            with self.subTest(name=hook_name):
                self.assertEqual(HookYOLODetector._normalize_class_name(hook_name), "hook")

        for lock_name in ("lock", "lifting lock", "lifting_lock", "lifting-lock"):
            with self.subTest(name=lock_name):
                self.assertEqual(HookYOLODetector._normalize_class_name(lock_name), "lock")

    def test_hook_lock_contact_latches_until_lock_disappears(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        hook = {"xyxy": [10, 10, 21, 21]}
        lock = {"xyxy": [20, 20, 30, 30]}

        self.assertTrue(processor._update_hook_lock_contact(hook, lock))
        self.assertFalse(processor._update_hook_lock_contact(None, lock))
        self.assertFalse(processor._update_hook_lock_contact(hook, None))

    def test_hook_lock_contact_includes_touching_box_edges(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        hook = {"xyxy": [10, 10, 20, 20]}
        lock = {"xyxy": [20, 20, 30, 30]}

        self.assertTrue(processor._update_hook_lock_contact(hook, lock))

    def test_lock_preview_smooths_box_jitter_but_does_not_keep_missing_lock(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        first = {"xyxy": [10, 10, 30, 30], "confidence": 0.8}
        second = {"xyxy": [12, 10, 32, 30], "confidence": 0.9}

        self.assertEqual(processor._smooth_lock_preview_box(first), first)
        smoothed = processor._smooth_lock_preview_box(second)

        self.assertEqual(smoothed["xyxy"], [11.3, 10.0, 31.3, 30.0])
        self.assertEqual(smoothed["confidence"], 0.9)
        self.assertIsNone(processor._smooth_lock_preview_box(None))

    def test_hook_lock_contact_allows_small_box_scaled_gap(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        hook = {"xyxy": [10, 10, 20, 40]}
        near_lock = {"xyxy": [24, 10, 34, 40]}
        distant_lock = {"xyxy": [28, 10, 38, 40]}

        self.assertTrue(processor._update_hook_lock_contact(hook, near_lock))
        self.assertFalse(processor._update_hook_lock_contact(hook, distant_lock))

    def test_hook_lock_percentages_increase_as_hook_approaches(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        hook = {"xyxy": [10, 10, 20, 40]}
        touching_lock = {"xyxy": [20, 10, 30, 40]}
        nearby_lock = {"xyxy": [40, 10, 50, 40]}
        farther_lock = {"xyxy": [80, 10, 90, 40]}
        distant_lock = {"xyxy": [200, 10, 210, 40]}

        self.assertEqual(
            processor._hook_lock_engagement_percentages(hook, None),
            (10, 0),
        )
        self.assertEqual(
            processor._hook_lock_engagement_percentages(None, touching_lock),
            (0, 10),
        )
        self.assertEqual(
            processor._hook_lock_engagement_percentages(hook, distant_lock),
            (10, 10),
        )
        nearby_progress = processor._hook_lock_engagement_percentages(
            hook, nearby_lock
        )
        farther_progress = processor._hook_lock_engagement_percentages(
            hook, farther_lock
        )
        self.assertGreater(nearby_progress[0], farther_progress[0])
        self.assertGreater(farther_progress[0], 10)
        self.assertEqual(nearby_progress[0], nearby_progress[1])
        self.assertEqual(farther_progress[0], farther_progress[1])
        self.assertEqual(
            processor._hook_lock_engagement_percentages(hook, touching_lock),
            (100, 100),
        )
        self.assertEqual(
            processor._hook_lock_engagement_percentages(
                hook, {"xyxy": [30, 10, 40, 40]}
            ),
            (93, 93),
        )

    def test_status_meter_smooths_contact_progress_across_frames(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        processor.detector = unittest.mock.Mock()
        processor.detector.model_paths = []
        processor.detector.detect.return_value = {
            "container": None,
            "hook": {"xyxy": [10, 10, 20, 40], "confidence": 0.8},
            "lock": {"xyxy": [20, 10, 30, 40], "confidence": 0.85},
        }
        processor.active_source = "rtsp"
        processor.latest_frame = None
        processor.camera_connected = False
        processor.camera_error = None
        processor.retrying = False

        processor._preview_lock_box = None
        with (
            patch.object(processor, "_draw_status"),
            patch.object(processor, "_speak_hook_lock_state"),
            patch.object(processor, "_maybe_speak_stable_location"),
        ):
            readings = []
            for _ in range(4):
                processor.process_frame(np.zeros((32, 32, 3), dtype=np.uint8))
                readings.append(processor.status_snapshot()["meter"])

        self.assertEqual(readings[0], 46)
        self.assertTrue(all(left < right for left, right in zip(readings, readings[1:])))
        self.assertLess(readings[-1], 100)

    def test_lock_contact_is_lift_ready_when_hook_is_outside_cavity(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        processor.detector = unittest.mock.Mock()
        processor.detector.model_paths = []
        processor.detector.detect.return_value = {
            "container": {"xyxy": [10, 10, 40, 40], "confidence": 0.9},
            "hook": {"xyxy": [42, 15, 48, 21], "confidence": 0.8},
            "lock": {"xyxy": [48, 15, 55, 21], "confidence": 0.85},
        }
        processor.active_source = "rtsp"
        processor.latest_frame = None
        processor.camera_connected = False
        processor.camera_error = None
        processor.retrying = False

        with (
            patch.object(processor, "_draw_status"),
            patch.object(processor, "_speak_hook_lock_state") as speak_lock_state,
            patch.object(processor, "_maybe_speak_stable_location"),
        ):
            processor.process_frame(np.zeros((32, 32, 3), dtype=np.uint8))

        status = processor.status_snapshot()
        self.assertTrue(status["hook_lock_contact"])
        self.assertTrue(status["hook_engaged"])
        self.assertTrue(status["lift_ready"])
        self.assertEqual(status["meter"], 100)
        self.assertEqual(status["hook_lock_state"], STATE_HOOK_LOCK_ENGAGED)
        self.assertEqual(
            status["hook_lock_command_hi"],
            STATUS_TEXT[STATE_HOOK_LOCK_ENGAGED],
        )
        speak_lock_state.assert_called_once_with(STATE_HOOK_LOCK_ENGAGED)

    def test_lock_contact_is_lift_ready_without_cavity(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        processor.detector = unittest.mock.Mock()
        processor.detector.detect.return_value = {
            "container": None,
            "hook": {"xyxy": [10, 10, 21, 21], "confidence": 0.8},
            "lock": {"xyxy": [20, 20, 30, 30], "confidence": 0.85},
        }
        processor.active_source = "rtsp"
        processor.latest_frame = None
        processor.camera_connected = False
        processor.camera_error = None
        processor.retrying = False

        with (
            patch.object(processor, "_draw_status"),
            patch.object(processor, "_speak_hook_lock_state") as speak_lock_state,
            patch.object(processor, "_maybe_speak_stable_location"),
        ):
            processor.process_frame(np.zeros((32, 32, 3), dtype=np.uint8))

        status = processor.status_snapshot()
        self.assertTrue(status["hook_lock_contact"])
        self.assertFalse(status["cavity_detected"])
        self.assertTrue(status["lift_ready"])
        self.assertEqual(status["hook_lock_state"], STATE_HOOK_LOCK_ENGAGED)
        self.assertEqual(
            status["hook_lock_command_hi"],
            STATUS_TEXT[STATE_HOOK_LOCK_ENGAGED],
        )
        speak_lock_state.assert_called_once_with(STATE_HOOK_LOCK_ENGAGED)

    def test_lock_model_detections_are_reported_to_frontend_immediately(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        processor.detector = unittest.mock.Mock()
        processor.detector.model_paths = []
        processor.detector.detect.return_value = {
            "container": {"xyxy": [10, 10, 40, 40], "confidence": 0.9},
            "hook": {"xyxy": [15, 15, 20, 20], "confidence": 0.8},
            "lock": {"xyxy": [18, 18, 30, 30], "confidence": 0.85},
        }
        processor.active_source = "rtsp"
        processor.latest_frame = None
        processor.camera_connected = False
        processor.camera_error = None
        processor.retrying = False

        with (
            patch.object(processor, "_draw_status"),
            patch.object(processor, "_speak_hook_lock_state") as speak_lock_state,
            patch.object(processor, "_maybe_speak_stable_location") as speak_position,
        ):
            processor.process_frame(np.zeros((32, 32, 3), dtype=np.uint8))

        status = processor.status_snapshot()
        speak_lock_state.assert_called_once_with(STATE_HOOK_LOCK_ENGAGED)
        speak_position.assert_not_called()
        self.assertEqual(status["state_key"], STATE_HOOK_INSIDE_LUG)
        self.assertTrue(status["hook_engaged"])
        self.assertTrue(status["lift_ready"])
        self.assertTrue(status["cavity_detected"])
        self.assertTrue(status["hook_detected"])
        self.assertTrue(status["lock_detected"])
        self.assertEqual(status["hook_engagement_percent"], 46)
        self.assertEqual(status["lock_engagement_percent"], 46)
        self.assertEqual(status["meter"], 46)
        self.assertEqual(status["hook_lock_state"], STATE_HOOK_LOCK_ENGAGED)
        self.assertEqual(
            status["hook_lock_command"],
            "Hook is engaged with lock. Ready to lift.",
        )
        self.assertEqual(
            status["hook_lock_command_hi"],
            STATUS_TEXT[STATE_HOOK_LOCK_ENGAGED],
        )
        self.assertEqual(status["alert_message_hi"], STATUS_TEXT[STATE_HOOK_INSIDE_LUG])

    def test_lock_missing_keeps_frontend_in_do_not_lift_state(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        processor.tts = None
        processor._preview_lock_box = {
            "xyxy": [18, 18, 30, 30],
            "confidence": 0.85,
        }
        processor.detector = unittest.mock.Mock()
        processor.detector.model_paths = []
        processor.detector.detect.return_value = {
            "container": {"xyxy": [10, 10, 40, 40], "confidence": 0.9},
            "hook": {"xyxy": [42, 15, 47, 20], "confidence": 0.8},
            "lock": None,
        }
        processor.active_source = "rtsp"
        processor.latest_frame = None
        processor.camera_connected = False
        processor.camera_error = None
        processor.retrying = False

        with (
            patch.object(processor, "_draw_status"),
            patch.object(processor, "_speak_hook_lock_state") as speak_lock_state,
            patch.object(processor, "_maybe_speak_stable_location") as speak_position,
        ):
            processor.process_frame(np.zeros((32, 32, 3), dtype=np.uint8))

        speak_position.assert_not_called()
        speak_lock_state.assert_called_once_with(STATE_HOOK_LOCK_NOT_ENGAGED)
        status = processor.status_snapshot()
        self.assertEqual(status["state_key"], STATE_HOOK_OUTSIDE_LUG)
        self.assertFalse(status["hook_engaged"])
        self.assertFalse(status["lift_ready"])
        self.assertIsNone(processor._preview_overlay[2])
        self.assertEqual(status["hook_lock_state"], STATE_HOOK_LOCK_NOT_ENGAGED)
        self.assertEqual(
            status["hook_lock_command"],
            "Hook is not engaged with lock. Do not lift.",
        )
        self.assertEqual(
            status["hook_lock_command_hi"],
            STATUS_TEXT[STATE_HOOK_LOCK_NOT_ENGAGED],
        )
        self.assertEqual(status["lift"], "Do Not Lift")
        self.assertEqual(status["alert_main"], DISPLAY_STATUS_TEXT[STATE_HOOK_OUTSIDE_LUG])
        self.assertEqual(status["alert_message_hi"], STATUS_TEXT[STATE_HOOK_OUTSIDE_LUG])

    def test_hook_lock_voice_alerts_use_hindi_status_text(self):
        for state_key in (STATE_HOOK_LOCK_ENGAGED, STATE_HOOK_LOCK_NOT_ENGAGED):
            with self.subTest(state=state_key):
                processor = HookStreamProcessor.__new__(HookStreamProcessor)
                processor.tts = unittest.mock.Mock()
                processor.tts.speak_sequence.return_value = True
                processor._tts_hook_lock_state = None
                processor._tts_channel = "camera-1"

                processor._speak_hook_lock_state(state_key)

                processor.tts.speak_sequence.assert_called_once_with(
                    STATUS_TEXT[state_key],
                    channel="camera-1",
                )

    def test_engaged_status_clears_when_hook_disappears(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        processor.detector = unittest.mock.Mock()
        processor.detector.detect.side_effect = [
            {
                "container": {"xyxy": [10, 10, 40, 40], "confidence": 0.9},
                "hook": {"xyxy": [15, 15, 22, 22], "confidence": 0.8},
                "lock": {"xyxy": [20, 20, 30, 30], "confidence": 0.85},
            },
            {
                "container": {"xyxy": [10, 10, 40, 40], "confidence": 0.9},
                "hook": None,
                "lock": {"xyxy": [20, 20, 30, 30], "confidence": 0.85},
            },
            {
                "container": {"xyxy": [10, 10, 40, 40], "confidence": 0.9},
                "hook": None,
                "lock": None,
            },
        ]
        processor.active_source = "rtsp"
        processor.latest_frame = None
        processor.camera_connected = False
        processor.camera_error = None
        processor.retrying = False

        with (
            patch.object(processor, "_draw_status"),
            patch.object(processor, "_speak_hook_lock_state") as speak_lock_state,
            patch.object(processor, "_maybe_speak_stable_location"),
        ):
            processor.process_frame(np.zeros((32, 32, 3), dtype=np.uint8))
            self.assertTrue(processor.status_snapshot()["lift_ready"])
            processor.process_frame(np.zeros((32, 32, 3), dtype=np.uint8))
            status = processor.status_snapshot()
            self.assertFalse(status["lift_ready"])
            self.assertIsNone(status["hook_lock_state"])
            self.assertEqual(status["hook_lock_command"], "")
            self.assertEqual(status["hook_lock_command_hi"], "")
            speak_lock_state.assert_called_once_with(STATE_HOOK_LOCK_ENGAGED)
            processor.process_frame(np.zeros((32, 32, 3), dtype=np.uint8))

        status = processor.status_snapshot()
        self.assertEqual(status["state_key"], STATE_HOOK_NOT_ENGAGED)
        self.assertFalse(status["lift_ready"])

    def test_legacy_command_remains_when_hook_is_not_detected(self):
        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        processor.detector = unittest.mock.Mock()
        processor.detector.detect.return_value = {
            "container": {"xyxy": [10, 10, 40, 40], "confidence": 0.9},
            "hook": None,
            "lock": {"xyxy": [18, 18, 30, 30], "confidence": 0.85},
        }
        processor.active_source = "rtsp"
        processor.latest_frame = None
        processor.camera_connected = False
        processor.camera_error = None
        processor.retrying = False

        with (
            patch.object(processor, "_maybe_speak_stable_location"),
            patch.object(processor, "_speak_hook_lock_state") as speak_lock_state,
        ):
            processor.process_frame(np.zeros((32, 32, 3), dtype=np.uint8))

        status = processor.status_snapshot()
        self.assertEqual(status["state_key"], STATE_HOOK_NOT_ENGAGED)
        self.assertEqual(status["alert_main"], DISPLAY_STATUS_TEXT[STATE_HOOK_NOT_ENGAGED])
        self.assertEqual(status["alert_message_hi"], STATUS_TEXT[STATE_HOOK_NOT_ENGAGED])
        self.assertIsNone(status["hook_lock_state"])
        self.assertEqual(status["hook_lock_command"], "")
        self.assertEqual(status["hook_lock_command_hi"], "")
        self.assertFalse(status["lift_ready"])
        speak_lock_state.assert_not_called()

    def test_detector_uses_highest_confidence_box_for_each_class(self):
        class CoordinateTensor:
            def __init__(self, values):
                self.values = values

            def cpu(self):
                return self

            def numpy(self):
                return np.asarray(self.values)

            def tolist(self):
                return self.values

        class Boxes:
            cls = [0, 1, 0, 1, 2]
            conf = [0.9, 0.7, 0.6, 0.95, 0.88]
            xyxy = [
                CoordinateTensor([10, 10, 100, 300]),
                CoordinateTensor([20, 100, 40, 200]),
                CoordinateTensor([0, 0, 50, 50]),
                CoordinateTensor([60, 100, 80, 200]),
                CoordinateTensor([70, 120, 110, 250]),
            ]

            def __len__(self):
                return len(self.cls)

        result = unittest.mock.Mock()
        result.boxes = Boxes()
        result.names = {0: "container", 1: "hook", 2: "lock"}
        model = unittest.mock.Mock(return_value=[result])
        detector = HookYOLODetector.__new__(HookYOLODetector)
        detector.models = [model]
        detector.confidence = 0.25
        detector._inference_lock = threading.Lock()

        detections = detector.detect(np.zeros((320, 320, 3), dtype=np.uint8))

        self.assertEqual(detections["container"]["confidence"], 0.9)
        self.assertEqual(detections["container"]["xyxy"], [10.0, 10.0, 100.0, 300.0])
        self.assertEqual(detections["hook"]["confidence"], 0.95)
        self.assertEqual(detections["hook"]["xyxy"], [60.0, 100.0, 80.0, 200.0])
        self.assertEqual(detections["lock"]["confidence"], 0.88)
        self.assertEqual(detections["lock"]["xyxy"], [70.0, 120.0, 110.0, 250.0])

    def test_torchvision_nms_fallback(self):
        import torch

        _prepare_torchvision_nms()
        import torchvision

        boxes = torch.tensor([[0, 0, 10, 10], [1, 1, 9, 9], [20, 20, 30, 30]], dtype=torch.float32)
        scores = torch.tensor([0.9, 0.8, 0.7])

        self.assertEqual(torchvision.ops.nms(boxes, scores, 0.5).tolist(), [0, 2])

    def test_classifier_reports_only_inside_or_outside(self):
        container = {"xyxy": [0, 100, 100, 300]}
        inside_hooks = {
            "center": {"xyxy": [45, 145, 55, 155]},
            "left-edge-touch": {"xyxy": [0, 145, 10, 155]},
            "right-edge-touch": {"xyxy": [90, 145, 100, 155]},
            "partially-overlapping-left": {"xyxy": [-10, 145, 5, 155]},
            "partially-overlapping-right": {"xyxy": [95, 145, 105, 155]},
        }
        outside_hooks = {
            "left": {"xyxy": [-20, 145, -10, 155]},
            "right": {"xyxy": [110, 145, 120, 155]},
            "above-but-horizontally-aligned": {"xyxy": [45, 50, 55, 60]},
            "below-but-horizontally-aligned": {"xyxy": [45, 310, 55, 320]},
        }

        for direction, hook in inside_hooks.items():
            with self.subTest(direction=direction):
                status, _, state = classify_hook_position(container, hook)
                self.assertEqual(status, DISPLAY_STATUS_TEXT[STATE_HOOK_INSIDE_LUG])
                self.assertEqual(state, STATE_HOOK_INSIDE_LUG)

        for direction, hook in outside_hooks.items():
            with self.subTest(direction=direction):
                status, _, state = classify_hook_position(container, hook)
                self.assertEqual(status, DISPLAY_STATUS_TEXT[STATE_HOOK_OUTSIDE_LUG])
                self.assertEqual(state, STATE_HOOK_OUTSIDE_LUG)

    def test_classifier_keeps_missing_lug_and_hook_states(self):
        container = {"xyxy": [0, 100, 100, 300]}

        self.assertEqual(classify_hook_position(None, None)[2], STATE_GAP_MISSING)
        self.assertEqual(classify_hook_position(container, None)[2], STATE_HOOK_NOT_ENGAGED)

    def test_tts_announces_only_lifting_lug_command_when_hook_is_missing(self):
        processor = self._make_processor()
        container = {"xyxy": [0, 100, 100, 300]}

        with patch("hook_detector.stream.time.monotonic", side_effect=[0.0, 2.9, 3.0, 7.0, 10.0, 20.0]):
            processor._maybe_speak_stable_location(
                container, None, STATE_HOOK_NOT_ENGAGED
            )
            processor._maybe_speak_stable_location(
                container, None, STATE_HOOK_NOT_ENGAGED
            )
            processor._maybe_speak_stable_location(
                container, None, STATE_HOOK_NOT_ENGAGED
            )
            processor._maybe_speak_stable_location(
                container, None, STATE_HOOK_NOT_ENGAGED
            )
            processor._maybe_speak_stable_location(
                container, None, STATE_HOOK_NOT_ENGAGED
            )
            processor._maybe_speak_stable_location(
                container, None, STATE_HOOK_NOT_ENGAGED
            )

        self.assertEqual(processor.tts.messages, [LIFTING_LUG_VISIBLE_TEXT] * 2)

    def test_tts_announces_binary_location_after_four_seconds(self):
        processor = self._make_processor()
        container = {"xyxy": [0, 100, 100, 300]}
        hook = {"xyxy": [45, 145, 55, 155]}

        with patch("hook_detector.stream.time.monotonic", side_effect=[0.0, 3.9, 4.0]):
            for _ in range(3):
                processor._maybe_speak_stable_location(
                    container, hook, STATE_HOOK_INSIDE_LUG
                )

        self.assertEqual(processor.tts.messages, [STATUS_TEXT[STATE_HOOK_INSIDE_LUG]] * 2)

    def test_tts_retries_binary_location_when_audio_queue_is_full(self):
        processor = self._make_processor()
        container = {"xyxy": [0, 100, 100, 300]}
        hook = {"xyxy": [45, 145, 55, 155]}
        queue_results = iter((False, True))
        attempts = []

        def speak_sequence(*messages, channel=None):
            attempts.append(messages)
            queued = next(queue_results)
            if queued:
                processor.tts.messages.extend(messages)
            return queued

        processor.tts.speak_sequence = speak_sequence
        with patch("hook_detector.stream.time.monotonic", side_effect=[0.0, 4.0, 4.1]):
            for _ in range(3):
                processor._maybe_speak_stable_location(
                    container, hook, STATE_HOOK_INSIDE_LUG
                )

        expected_messages = (STATUS_TEXT[STATE_HOOK_INSIDE_LUG],) * 2
        self.assertEqual(attempts, [expected_messages, expected_messages])
        self.assertEqual(processor.tts.messages, list(expected_messages))

    def test_tts_does_not_cancel_queued_lug_repeats_when_hook_is_detected(self):
        processor = self._make_processor()
        container = {"xyxy": [0, 100, 100, 300]}
        hook = {"xyxy": [45, 145, 55, 155]}

        processor._tts_hook_missing_alerted = True
        with patch("hook_detector.stream.time.monotonic", return_value=3.0):
            processor._maybe_speak_stable_location(
                container, hook, STATE_HOOK_INSIDE_LUG
            )

        self.assertEqual(processor.tts.cancel_count, 0)
        self.assertEqual(processor.tts.messages, [])

    def test_tts_does_not_repeat_lug_command_on_transient_hook_detection(self):
        processor = self._make_processor()
        container = {"xyxy": [0, 100, 100, 300]}
        hook = {"xyxy": [45, 145, 55, 155]}
        times = [0.0, 3.0, 4.0, 4.5, 8.0, 12.0]

        with patch("hook_detector.stream.time.monotonic", side_effect=times):
            processor._maybe_speak_stable_location(
                container, None, STATE_HOOK_NOT_ENGAGED
            )
            processor._maybe_speak_stable_location(
                container, None, STATE_HOOK_NOT_ENGAGED
            )
            processor._maybe_speak_stable_location(container, hook, STATE_HOOK_INSIDE_LUG)
            processor._maybe_speak_stable_location(
                container, None, STATE_HOOK_NOT_ENGAGED
            )
            processor._maybe_speak_stable_location(container, hook, STATE_HOOK_INSIDE_LUG)
            processor._maybe_speak_stable_location(container, hook, STATE_HOOK_INSIDE_LUG)

        self.assertEqual(processor.tts.messages, [LIFTING_LUG_VISIBLE_TEXT] * 2 + [STATUS_TEXT[STATE_HOOK_INSIDE_LUG]] * 2)
        self.assertEqual(processor.tts.cancel_count, 0)

    def test_tts_prefixes_and_repeats_alert_for_camera_side(self):
        processor = self._make_processor()
        processor.camera_side = "right"
        container = {"xyxy": [0, 100, 100, 300]}
        hook = {"xyxy": [45, 145, 55, 155]}

        with patch("hook_detector.stream.time.monotonic", side_effect=[0.0, 4.0]):
            processor._maybe_speak_stable_location(
                container, hook, STATE_HOOK_INSIDE_LUG
            )
            processor._maybe_speak_stable_location(
                container, hook, STATE_HOOK_INSIDE_LUG
            )

        expected_message = f"Lifting hook 2. {STATUS_TEXT[STATE_HOOK_INSIDE_LUG]}"
        self.assertEqual(processor.tts.messages, [expected_message] * 2)
        self.assertEqual(processor.tts.channels, [processor._tts_channel])

    def test_lug_command_repeats_twice_without_hook_prefix_for_each_camera(self):
        processor_one = self._make_processor()
        processor_one.camera_side = "left"
        processor_one._tts_channel = "Bay-01-Camera-1"
        processor_two = self._make_processor()
        processor_two.camera_side = "right"
        processor_two._tts_channel = "Bay-01-Camera-2"
        processor_two.tts = processor_one.tts
        container = {"xyxy": [0, 100, 100, 300]}

        with patch("hook_detector.stream.time.monotonic", side_effect=[0.0, 3.0, 0.0, 3.0]):
            for processor in (processor_one, processor_two):
                processor._maybe_speak_stable_location(
                    container, None, STATE_HOOK_NOT_ENGAGED
                )
                processor._maybe_speak_stable_location(
                    container, None, STATE_HOOK_NOT_ENGAGED
                )

        self.assertEqual(
            processor_one.tts.messages,
            [LIFTING_LUG_VISIBLE_TEXT] * 4,
        )
        self.assertEqual(
            processor_one.tts.channels,
            ["Bay-01-Camera-1", "Bay-01-Camera-2"],
        )

    def test_left_camera_repeats_alert_and_uses_its_own_tts_channel(self):
        processor = self._make_processor()
        processor.camera_side = "left"
        processor._tts_channel = "Bay-01-Camera-1"
        container = {"xyxy": [0, 100, 100, 300]}
        hook = {"xyxy": [45, 145, 55, 155]}

        with patch("hook_detector.stream.time.monotonic", side_effect=[0.0, 4.0]):
            processor._maybe_speak_stable_location(container, hook)
            processor._maybe_speak_stable_location(container, hook)

        expected_message = f"Lifting hook 1. {STATUS_TEXT[STATE_HOOK_INSIDE_LUG]}"
        self.assertEqual(processor.tts.messages, [expected_message] * 2)
        self.assertEqual(processor.tts.channels, ["Bay-01-Camera-1"])

    def test_tts_cancels_only_the_stream_where_both_detections_disappear(self):
        tts = TTSManager.__new__(TTSManager)
        tts._messages = Queue(maxsize=4)
        tts._sequence_lock = threading.Lock()
        tts._sequence_generation = 0
        tts._channel_generations = {}
        tts._cancel_event = threading.Event()
        tts._player_lock = threading.Lock()
        tts._player = None
        tts._active_channel = None

        tts.speak_sequence("Left command", "Left command", channel="left")
        tts.speak_sequence("Right command", "Right command", channel="right")
        processor = self._make_processor()
        processor.tts = tts
        processor._tts_channel = "left"
        processor._maybe_speak_stable_location(None, None)

        remaining = tts._messages.get_nowait()
        self.assertEqual(remaining.channel, "right")
        self.assertEqual(remaining.messages, ("Right command", "Right command"))
        self.assertTrue(tts._messages.empty())

    def test_tts_does_not_announce_when_both_objects_are_out_of_frame(self):
        processor = self._make_processor()

        with patch("hook_detector.stream.time.monotonic", return_value=0.0):
            processor._maybe_speak_stable_location(
                None, None, STATE_GAP_MISSING
            )

        self.assertEqual(processor.tts.messages, [])
        self.assertEqual(processor.tts.cancel_count, 1)

    def test_tts_cancels_queued_alert_when_both_objects_disappear(self):
        processor = self._make_processor()
        processor.tts.messages.append("Queued safety alert")

        processor._maybe_speak_stable_location(
            None, None, STATE_GAP_MISSING
        )

        self.assertEqual(processor.tts.cancel_count, 1)
        self.assertEqual(processor.tts.messages, [])

    def test_tts_speak_only_queues_audio_work(self):
        tts = TTSManager.__new__(TTSManager)
        tts._stop_event = threading.Event()
        tts._player_lock = threading.Lock()
        tts._player = None
        tts._messages = Queue(maxsize=1)

        with patch.object(tts, "_speak_now") as play_audio:
            tts.speak("Alert")

        play_audio.assert_not_called()
        self.assertEqual(tts._messages.get_nowait(), "Alert")

    def test_tts_queues_cancelable_alert_sequence_as_one_item(self):
        tts = TTSManager.__new__(TTSManager)
        tts._messages = Queue(maxsize=1)
        tts._sequence_lock = threading.Lock()
        tts._sequence_generation = 0
        tts._cancel_event = threading.Event()
        tts._player_lock = threading.Lock()
        tts._player = None

        self.assertTrue(tts.speak_sequence("First", "Second"))
        queued = tts._messages.get_nowait()
        self.assertEqual(queued.messages, ("First", "Second"))
        tts._messages.put_nowait(queued)
        tts.cancel_pending()
        self.assertTrue(tts._messages.empty())
        self.assertEqual(tts._sequence_generation, 1)

    def test_tts_plays_each_camera_hook_sequence_in_queue_order(self):
        tts = TTSManager.__new__(TTSManager)
        tts._messages = Queue(maxsize=4)
        tts._sequence_lock = threading.Lock()
        tts._sequence_generation = 0
        tts._channel_generations = {}
        tts._cancel_event = threading.Event()
        tts._stop_event = threading.Event()
        tts._player_lock = threading.Lock()
        tts._player = None
        tts._active_channel = None
        tts.speak_sequence("Left hook command", "Left hook command", channel="left")
        tts.speak_sequence("Right hook command", "Right hook command", channel="right")
        played_messages = []

        def play_message(message):
            played_messages.append(message)
            if played_messages.count("Right hook command") == 2:
                tts._stop_event.set()

        with patch.object(tts, "_speak_now", side_effect=play_message):
            tts._play_messages()

        self.assertEqual(
            played_messages,
            [
                "Left hook command",
                "Left hook command",
                "Right hook command",
                "Right hook command",
            ],
        )

    def test_tts_cancel_stops_remaining_sequence_messages(self):
        tts = TTSManager.__new__(TTSManager)
        tts._messages = Queue(maxsize=1)
        tts._sequence_lock = threading.Lock()
        tts._sequence_generation = 0
        tts._cancel_event = threading.Event()
        tts._stop_event = threading.Event()
        tts._player_lock = threading.Lock()
        tts._player = None
        tts.speak_sequence("First", "Second")

        def cancel_after_first(message):
            tts.cancel_pending()
            tts._stop_event.set()

        with patch.object(tts, "_speak_now", side_effect=cancel_after_first) as play_audio:
            tts._play_messages()

        self.assertEqual(play_audio.call_count, 1)
        self.assertEqual(play_audio.call_args.args, ("First",))

    def test_tts_cancel_terminates_active_audio_player(self):
        tts = TTSManager.__new__(TTSManager)
        tts._messages = Queue(maxsize=1)
        tts._sequence_lock = threading.Lock()
        tts._sequence_generation = 0
        tts._cancel_event = threading.Event()
        tts._player_lock = threading.Lock()
        tts._player = unittest.mock.Mock()

        tts.cancel_pending()

        self.assertTrue(tts._cancel_event.is_set())
        tts._player.terminate.assert_called_once_with()

    def test_linux_tts_plays_wav_through_default_audio_player(self):
        tts = TTSManager.__new__(TTSManager)
        tts._cancel_event = threading.Event()
        tts._stop_event = threading.Event()
        tts._player_lock = threading.Lock()
        tts._player = None

        def synthesize_wav(message, wav_file):
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
            wav_file.writeframes(b"\x00\x00")

        tts.voice = unittest.mock.Mock(synthesize_wav=synthesize_wav)
        with (
            patch("hook_detector.tts.os.name", "posix"),
            patch("hook_detector.tts.shutil.which", side_effect=lambda name: "/usr/bin/paplay" if name == "paplay" else None),
            patch("hook_detector.tts.subprocess.Popen") as play_audio,
        ):
            play_audio.return_value.wait.return_value = 0
            tts._speak_now("Alert")

        self.assertEqual(play_audio.call_args.args[0][0], "/usr/bin/paplay")
        play_audio.return_value.wait.assert_called_once_with(timeout=0.1)

    def test_linux_tts_routes_piper_audio_to_configured_sink(self):
        tts = TTSManager.__new__(TTSManager)
        tts._cancel_event = threading.Event()
        tts._stop_event = threading.Event()
        tts._player_lock = threading.Lock()
        tts._player = None

        def synthesize_wav(message, wav_file):
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
            wav_file.writeframes(b"\x00\x00")

        tts.voice = unittest.mock.Mock(synthesize_wav=synthesize_wav)
        with (
            patch("hook_detector.tts.os.name", "posix"),
            patch.dict(os.environ, {"NMDC_HOOK_AUDIO_SINK": "bluez_output.test"}),
            patch("hook_detector.tts.shutil.which", side_effect=lambda name: f"/usr/bin/{name}" if name == "paplay" else None),
            patch("hook_detector.tts.subprocess.Popen") as play_audio,
        ):
            play_audio.return_value.wait.return_value = 0
            tts._speak_now("Alert")

        self.assertEqual(play_audio.call_args.args[0][:3], ["/usr/bin/paplay", "--device", "bluez_output.test"])

    def test_tts_stop_terminates_active_audio_player(self):
        tts = TTSManager.__new__(TTSManager)
        tts._stop_event = threading.Event()
        tts._player_lock = threading.Lock()
        tts._messages = Queue(maxsize=1)
        tts._worker = unittest.mock.Mock()
        tts._player = unittest.mock.Mock()

        tts.stop()

        self.assertTrue(tts._stop_event.is_set())
        tts._player.terminate.assert_called_once_with()

    def test_tts_repeats_binary_location_alert_every_five_seconds(self):
        processor = self._make_processor()
        container = {"xyxy": [0, 100, 100, 300]}
        hook = {"xyxy": [45, 145, 55, 155]}

        with patch("hook_detector.stream.time.monotonic", side_effect=[0.0, 3.9, 4.0, 8.9, 9.0]):
            processor._maybe_speak_stable_location(container, hook)
            processor._maybe_speak_stable_location(container, hook)
            processor._maybe_speak_stable_location(container, hook)
            processor._maybe_speak_stable_location(container, hook)
            processor._maybe_speak_stable_location(container, hook)

        self.assertEqual(processor.tts.messages, [STATUS_TEXT[STATE_HOOK_INSIDE_LUG]] * 4)

    def test_tts_uses_only_inside_or_outside_voice_alerts(self):
        processor = self._make_processor()
        container = {"xyxy": [0, 100, 100, 300]}
        inside_hook = {"xyxy": [45, 145, 55, 155]}
        outside_hook = {"xyxy": [110, 145, 120, 155]}

        with patch("hook_detector.stream.time.monotonic", side_effect=[0.0, 4.0, 5.0, 9.0]):
            processor._maybe_speak_stable_location(
                container, inside_hook, STATE_HOOK_INSIDE_LUG
            )
            processor._maybe_speak_stable_location(
                container, inside_hook, STATE_HOOK_INSIDE_LUG
            )
            processor._maybe_speak_stable_location(
                container, outside_hook, STATE_HOOK_OUTSIDE_LUG
            )
            processor._maybe_speak_stable_location(
                container, outside_hook, STATE_HOOK_OUTSIDE_LUG
            )

        self.assertEqual(
            processor.tts.messages,
            [STATUS_TEXT[STATE_HOOK_INSIDE_LUG]] * 2 + [STATUS_TEXT[STATE_HOOK_OUTSIDE_LUG]] * 2,
        )

    def test_tts_restarts_location_delay_after_hook_disappears(self):
        processor = self._make_processor()
        container = {"xyxy": [0, 100, 100, 300]}
        hook = {"xyxy": [45, 145, 55, 155]}
        moved_hook = {"xyxy": [55, 145, 65, 155]}
        times = [0.0, 4.0, 4.5, 5.0, 10.0, 14.0]

        with patch("hook_detector.stream.time.monotonic", side_effect=times):
            processor._maybe_speak_stable_location(container, hook)
            processor._maybe_speak_stable_location(container, hook)
            processor._maybe_speak_stable_location(container, moved_hook)
            processor._maybe_speak_stable_location(
                container, None, STATE_HOOK_NOT_ENGAGED
            )
            processor._maybe_speak_stable_location(container, hook)
            processor._maybe_speak_stable_location(container, hook)

        self.assertEqual(
            processor.tts.messages,
            [
                STATUS_TEXT[STATE_HOOK_INSIDE_LUG],
                STATUS_TEXT[STATE_HOOK_INSIDE_LUG],
                LIFTING_LUG_VISIBLE_TEXT,
                LIFTING_LUG_VISIBLE_TEXT,
                STATUS_TEXT[STATE_HOOK_INSIDE_LUG],
                STATUS_TEXT[STATE_HOOK_INSIDE_LUG],
            ],
        )
        self.assertEqual(processor.tts.cancel_count, 0)

    @staticmethod
    def _make_processor():
        class FakeTTS:
            def __init__(self):
                self.messages = []
                self.cancel_count = 0
                self.channels = []

            def speak(self, message):
                self.messages.append(message)
                return True

            def speak_sequence(self, *messages, channel=None):
                self.messages.extend(messages)
                self.channels.append(channel)
                return True

            def cancel_pending(self, channel=None):
                self.cancel_count += 1
                self.channels.append(channel)
                self.messages.clear()

        processor = HookStreamProcessor.__new__(HookStreamProcessor)
        processor.tts = FakeTTS()
        processor._tts_lug_visible_since = None
        processor._tts_stable_since = None
        processor._tts_location_state = None
        processor._tts_location_last_alert = None
        processor._tts_hook_missing_alerted = False
        processor._tts_channel = "test-camera"
        return processor


if __name__ == "__main__":
    unittest.main()
