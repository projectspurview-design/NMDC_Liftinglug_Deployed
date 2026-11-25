from __future__ import annotations

import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

LOGGER = logging.getLogger(__name__)
RECORDING_LIMIT = 10


def default_recordings_directory() -> Path:
    configured_directory = os.getenv("HOOK_DETECTOR_DATA_DIR")
    data_directory = (
        Path(configured_directory).expanduser()
        if configured_directory
        else Path.home() / ".nmdc-hook-detector"
    )
    return data_directory / "recordings"


class DetectionVideoArchive:
    def __init__(
        self,
        directory: str | Path | None = None,
        limit: int | None = None,
    ) -> None:
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
            raise ValueError("Recording limit must be a positive integer.")

        self.directory = Path(directory).expanduser() if directory else default_recordings_directory()
        self.in_progress_directory = self.directory / ".in-progress"
        self.limit = limit
        self._lock = threading.Lock()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.in_progress_directory.mkdir(parents=True, exist_ok=True)
        self._remove_incomplete_recordings()
        with self._lock:
            self._prune_old_recordings()

    def create_recording_path(self, camera_id: str | None = None) -> Path:
        safe_camera_id = re.sub(r"[^A-Za-z0-9_-]", "_", camera_id or "camera")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        unique_id = uuid.uuid4().hex[:8]
        return self.in_progress_directory / f"detection_{timestamp}_{safe_camera_id}_{unique_id}.mp4"

    def complete_recording(self, recording_path: str | Path) -> Path:
        path = self._validate_in_progress_path(recording_path)
        if not path.is_file() or path.stat().st_size == 0:
            self.discard_recording(path)
            raise RuntimeError(f"Detection recording is empty: {path}")

        completed_path = self.directory / path.name
        with self._lock:
            os.replace(path, completed_path)
            self._prune_old_recordings()
        return completed_path

    def discard_recording(self, recording_path: str | Path) -> None:
        path = self._validate_in_progress_path(recording_path)
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def _validate_in_progress_path(self, recording_path: str | Path) -> Path:
        path = Path(recording_path)
        if path.parent.resolve() != self.in_progress_directory.resolve():
            raise ValueError("Recording path is outside the in-progress directory.")
        return path

    def _remove_incomplete_recordings(self) -> None:
        for path in self.in_progress_directory.glob("detection_*.mp4"):
            try:
                path.unlink()
            except OSError:
                LOGGER.exception("Could not remove incomplete detection recording %s.", path)

    def _prune_old_recordings(self) -> None:
        recordings = sorted(
            self.directory.glob("detection_*.mp4"),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
        )
        from .deployment import SETTINGS
        import time
        import shutil
        cutoff = time.time() - 86400 * SETTINGS["retention_days"]
        for i, path in enumerate(recordings):
            usage = shutil.disk_usage(self.directory)
            if (path.stat().st_mtime >= cutoff and usage.free/usage.total >= SETTINGS["reserve_percent"]/100
                    and (self.limit is None or i >= len(recordings)-self.limit)):
                break
            try:
                path.unlink()
            except OSError:
                LOGGER.exception("Could not remove old detection recording %s.", path)
                raise
