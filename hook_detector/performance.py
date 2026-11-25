from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import threading
from typing import Any

import psutil

from .stream import _npu_utilization_snapshot


def _temperature_reading() -> dict[str, float | str] | None:
    try:
        sensors = psutil.sensors_temperatures(fahrenheit=False)
    except (AttributeError, NotImplementedError, OSError):
        return None

    readings = []
    cpu_terms = ("cpu", "core", "package", "tctl", "tdie", "soc")
    for chip, entries in sensors.items():
        for entry in entries:
            value = entry.current
            label = (entry.label or chip).strip()
            if value is None or not any(term in f"{chip} {label}".lower() for term in cpu_terms):
                continue
            readings.append((float(value), label or chip))
    if not readings:
        return None
    value, label = max(readings, key=lambda reading: reading[0])
    return {"celsius": round(value, 1), "sensor": label}


class PerformanceMonitor:
    def __init__(self, history_limit: int = 180) -> None:
        self._history: deque[dict[str, Any]] = deque(maxlen=history_limit)
        self._lock = threading.Lock()

    def snapshot(self, processor: Any) -> dict[str, Any]:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        core_percent = [round(value, 1) for value in psutil.cpu_percent(interval=None, percpu=True)]
        cpu_percent = round(sum(core_percent) / len(core_percent), 1) if core_percent else None
        memory = psutil.virtual_memory()
        process = psutil.Process()
        process_cpu_percent = round(process.cpu_percent(interval=None), 1)
        process_rss_bytes = process.memory_info().rss
        npu = _npu_utilization_snapshot()
        inference_latency_ms = getattr(processor, "_inference_latency_ms", None)
        inference_interval = getattr(processor, "_inference_interval_s", None)
        inference_fps = (
            round(1 / inference_interval, 2)
            if isinstance(inference_interval, (int, float)) and inference_interval > 0
            else None
        )
        sample = {
            "timestamp": timestamp,
            "cpu_percent": cpu_percent,
            "memory_percent": round(memory.percent, 1),
            "npu_percent": npu["npu_utilization_percent"],
        }
        with self._lock:
            self._history.append(sample)
            history = list(self._history)

        return {
            "timestamp": timestamp,
            "cpu": {
                "utilization_percent": cpu_percent,
                "per_core_percent": core_percent,
                "process_percent": process_cpu_percent,
                "process_rss_bytes": process_rss_bytes,
            },
            "memory": {
                "used_bytes": memory.used,
                "available_bytes": memory.available,
                "total_bytes": memory.total,
                "percent": round(memory.percent, 1),
            },
            "temperature": _temperature_reading(),
            "accelerators": {
                "npu_utilization_percent": npu["npu_utilization_percent"],
                "npu_devices": npu["npu_devices"],
            },
            "inference": {
                "latency_ms": inference_latency_ms,
                "fps": inference_fps,
            },
            "history": history,
        }