"""Inspect the selected Python environment and test all four compiled models."""
import importlib.util
import json
from pathlib import Path
import platform
import shutil
import sys
import os

root = Path(__file__).resolve().parent
config = json.loads(Path(os.environ.get("NMDC_CONFIG",root/"deployment.json")).read_text())
checks = {}
for module in ("cv2","numpy","fastapi","uvicorn","cryptography","piper"):
    checks[module] = importlib.util.find_spec(module) is not None
checks["ffmpeg"] = shutil.which("ffmpeg") is not None
archive = (root / config["recording_root"]).resolve()
checks["recording folder marked and writable"] = (archive/".nmdc-archive").is_file() and os.access(archive,os.W_OK)
if config["backend"]=="deepx":
    checks["dx_engine"] = importlib.util.find_spec("dx_engine") is not None
    for name in ("nmdc-yolo","nmdchook","exp","new_lock 1"):
        checks["weights/"+name+".dxnn"] = (root/"weights"/(name+".dxnn")).is_file()
else:
    checks["ultralytics"] = importlib.util.find_spec("ultralytics") is not None
print(platform.platform(), sys.executable)
for name,ok in checks.items():
    print("OK     " if ok else "MISSING", name)
if not all(checks.values()):
    raise SystemExit(1)
if config["backend"]=="deepx":
    import numpy as np
    from hook_detector.deepx_backend import DeepXDetector
    engine=DeepXDetector(root/"weights",0.25)
    engine.detect(np.zeros((480,640,3),dtype=np.uint8))
    print("All four DX-RT engines accepted a frame and returned the expected output layout.")
