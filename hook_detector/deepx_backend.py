"""DX-M1 runner for the raw, decoded YOLO export produced by prepare_models.py."""
import threading
from collections import deque
from contextlib import contextmanager
from pathlib import Path

import cv2
import numpy as np

LABELS = {"nmdc-yolo": ("hook", "container"), "nmdchook": ("container", "hook"),
          "exp": ("container", "hook", "lock"),
          "new_lock 1": ("container", "hook", "lock")}


class DeepXDetector:
    def __init__(self, directory, confidence):
        paths = [Path(directory) / (name + ".dxnn") for name in LABELS]
        missing = [str(path) for path in paths if not path.is_file()]
        if missing:
            raise RuntimeError("Compile the supplied weights first. Missing: " + ", ".join(missing))
        from dx_engine import InferenceEngine
        self.model_paths = [str(path) for path in paths]
        self.engines = [InferenceEngine(str(path)) for path in paths]
        self.confidence = confidence
        self.waiters = deque()
        self.condition = threading.Condition()

    @contextmanager
    def turn(self):
        ticket = object()
        with self.condition:
            self.waiters.append(ticket)
            self.condition.wait_for(lambda: self.waiters[0] is ticket)
        try:
            yield
        finally:
            with self.condition:
                self.waiters.popleft()
                self.condition.notify_all()

    def detect(self, frame):
        with self.turn():
            h, w = frame.shape[:2]
            scale = min(640 / w, 640 / h)
            rw, rh = max(1, round(w * scale)), max(1, round(h * scale))
            x, y = (640-rw)//2, (640-rh)//2
            canvas = np.full((640, 640, 3), 114, dtype=np.uint8)
            canvas[y:y+rh, x:x+rw] = cv2.resize(frame, (rw, rh))
            tensor = np.ascontiguousarray(canvas[:, :, ::-1][None])
            result = dict.fromkeys(("container", "hook", "lock"))
            for engine, labels in zip(self.engines, LABELS.values()):
                outputs = engine.run(tensor)
                if len(outputs) != 1:
                    raise ValueError("Expected the single decoded YOLO output. Check compiler endpoints.")
                rows = np.asarray(outputs[0]).squeeze(0)
                if rows.ndim != 2 or rows.shape[0] != len(labels)+4 or rows.shape[1] < 100:
                    raise ValueError(f"Unsupported YOLO output shape {rows.shape}. See START_HERE.md")
                if not np.issubdtype(rows.dtype, np.floating) or not np.isfinite(rows).all():
                    raise ValueError("Expected finite dequantized model outputs")
                scores = rows[4:]
                for i, name in enumerate(labels):
                    indices = np.flatnonzero(scores[i] >= self.confidence)
                    if not indices.size:
                        continue
                    j = indices[scores[i, indices].argmax()]
                    score = float(scores[i,j])
                    if result[name] and result[name]["confidence"] >= score:
                        continue
                    cx, cy, bw, bh = rows[:4,j]
                    box = [(cx-bw/2-x)*w/rw,(cy-bh/2-y)*h/rh,
                           (cx+bw/2-x)*w/rw,(cy+bh/2-y)*h/rh]
                    box = np.clip(box, 0, [w,h,w,h]).tolist()
                    if box[2] > box[0] and box[3] > box[1]:
                        result[name] = dict(cls_name=name, confidence=score, xyxy=box)
            return result

    def draw_box(self, frame, box, label, color):
        if box:
            a,b,c,d = map(int, box["xyxy"])
            cv2.rectangle(frame,(a,b),(c,d),color,2)
            cv2.putText(frame,label,(a,max(12,b-6)),cv2.FONT_HERSHEY_SIMPLEX,.6,color,2)
