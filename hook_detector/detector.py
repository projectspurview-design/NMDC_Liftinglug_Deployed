from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import threading
from typing import Any, Dict, Optional

import cv2


from .config import CONFIDENCE_THRESHOLD, CONTAINER_CLASS, HOOK_CLASS, LOCK_CLASS


_torchvision_library = None


def _prepare_torchvision_nms() -> None:
    import torch
    from ultralytics.utils.nms import TorchNMS
    global _torchvision_library

    try:
        import torchvision
    except RuntimeError as error:
        if "operator torchvision::nms does not exist" not in str(error):
            raise
        _torchvision_library = torch.library.Library("torchvision", "FRAGMENT")
        _torchvision_library.define("nms(Tensor boxes, Tensor scores, float iou_threshold) -> Tensor")
        _torchvision_library.define("qnms(Tensor boxes, Tensor scores, float iou_threshold) -> Tensor")
        import torchvision

    if not torchvision.extension._has_ops():
        torchvision.ops.nms = TorchNMS.nms


class HookYOLODetector:
    def __init__(self, model_path: str | Sequence[str] | Path, conf: float = CONFIDENCE_THRESHOLD):
        from ultralytics import YOLO
        self.model_path = model_path
        self.confidence = conf
        _prepare_torchvision_nms()

        if isinstance(model_path, (str, Path)):
            self.model_paths = [str(model_path)]
        else:
            self.model_paths = [str(path) for path in model_path]

        self.models = [YOLO(path) for path in self.model_paths]
        self._inference_lock = threading.Lock()

    @staticmethod
    def _normalize_class_name(name: str) -> str:
        normalized = " ".join(str(name).lower().replace("_", " ").replace("-", " ").split())
        aliases = {
            "container": CONTAINER_CLASS,
            "cavity": CONTAINER_CLASS,
            "lug": CONTAINER_CLASS,
            "lifting lug": CONTAINER_CLASS,
            "hook": HOOK_CLASS,
            "lifting hook": HOOK_CLASS,
            "lock": LOCK_CLASS,
            "lifting lock": LOCK_CLASS,
        }
        return aliases.get(normalized, normalized)

    def detect(self, frame: Any) -> Dict[str, Optional[Dict[str, float]]]:
        with self._inference_lock:
            return self._detect_locked(frame)

    def _detect_locked(self, frame: Any) -> Dict[str, Optional[Dict[str, float]]]:
        detections: Dict[str, Optional[Dict[str, float]]] = {
            CONTAINER_CLASS: None,
            HOOK_CLASS: None,
            LOCK_CLASS: None,
        }

        for model in self.models:
            results = model(frame, conf=self.confidence, verbose=False)[0]
            boxes = results.boxes
            if boxes is None or len(boxes) == 0:
                continue

            for index in range(len(boxes)):
                cls_id = int(boxes.cls[index])
                if isinstance(results.names, dict):
                    raw_name = results.names.get(cls_id, str(cls_id))
                else:
                    raw_name = results.names[cls_id] if cls_id < len(results.names) else str(cls_id)
                cls_name = self._normalize_class_name(raw_name)
                conf = float(boxes.conf[index])
                xyxy = boxes.xyxy[index].cpu().numpy().tolist()

                if cls_name not in (CONTAINER_CLASS, HOOK_CLASS, LOCK_CLASS):
                    continue

                box_data = {
                    "cls_name": cls_name,
                    "confidence": conf,
                    "xyxy": [float(v) for v in xyxy],
                }
                current_detection = detections[cls_name]
                if current_detection is None or conf > current_detection["confidence"]:
                    detections[cls_name] = box_data

        return detections

    def draw_box(self, frame: Any, box: Optional[Dict[str, float]], label: str, color: tuple[int, int, int]) -> None:
        if box is None:
            return
        x1, y1, x2, y2 = [int(v) for v in box["xyxy"]]
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(frame, label, (x1, max(0, y1 - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)
