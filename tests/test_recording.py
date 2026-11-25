import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from hook_detector.recording import DetectionVideoArchive
from hook_detector.stream import HookStreamProcessor


class DetectionVideoArchiveTest(unittest.TestCase):
    def test_fifo_keeps_the_latest_ten_clips_across_cameras(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = DetectionVideoArchive(directory)
            completed = []

            for index in range(11):
                recording_path = archive.create_recording_path(f"camera-{index % 2}")
                recording_path.write_bytes(b"recorded video")
                os.utime(recording_path, ns=(index + 1, index + 1))
                completed.append(archive.complete_recording(recording_path))

            self.assertEqual(len(list(Path(directory).glob("detection_*.mp4"))), 10)
            self.assertFalse(completed[0].exists())
            self.assertTrue(all(path.exists() for path in completed[1:]))


class DetectionEventRecordingTest(unittest.TestCase):
    def test_records_one_clip_until_all_detected_objects_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = DetectionVideoArchive(directory)
            processor = HookStreamProcessor.__new__(HookStreamProcessor)
            processor.detector = Mock()
            processor.detector.detect.side_effect = [
                {
                    "container": {"xyxy": [4, 4, 20, 20], "confidence": 0.9},
                    "hook": None,
                },
                {
                    "container": None,
                    "hook": {"xyxy": [10, 10, 14, 14], "confidence": 0.8},
                },
                {"container": None, "hook": None},
            ]
            processor.video_archive = archive
            processor.camera_id = "Bay-01-Camera-1"
            processor.camera_side = None
            processor._recording_writer = None
            processor._recording_path = None
            processor._recording_last_frame_at = None
            processor.camera_connected = False
            processor.camera_error = None
            processor.retrying = False
            processor.active_source = "rtsp"
            frame = np.full((32, 32, 3), 120, dtype=np.uint8)
            writer = Mock()
            writer.isOpened.return_value = True
            writer.write.side_effect = lambda _: Path(writer_path[0]).write_bytes(b"video")
            writer_path = []

            def open_writer(path, *_args):
                writer_path.append(path)
                return writer

            with patch("hook_detector.stream.cv2.VideoWriter", side_effect=open_writer):
                with patch("hook_detector.stream.cv2.VideoWriter_fourcc", return_value=0):
                    with patch("hook_detector.stream.time.monotonic", side_effect=[100.0, 100.2]):
                        with patch.object(processor, "_render_preview_frame", return_value=frame):
                            with patch.object(processor, "_maybe_speak_stable_location"):
                                for _ in range(3):
                                    processor.process_frame(frame, publish_preview=False)

            self.assertEqual(writer.write.call_count, 3)
            writer.release.assert_called_once_with()
            recordings = list(Path(directory).glob("detection_*.mp4"))
            self.assertEqual(len(recordings), 1)
