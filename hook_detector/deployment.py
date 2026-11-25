import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = json.loads(Path(os.environ.get("NMDC_CONFIG",ROOT/"deployment.json")).read_text(encoding="utf-8"))
SETTINGS.setdefault("event_retention_days", 365)
for key in ("analysis_fps","stale_seconds","segment_seconds","retention_days","event_retention_days"):
    if not 0 < SETTINGS[key] < 100000:
        raise ValueError(f"Invalid setting: {key}")
if not 0 <= SETTINGS["reserve_percent"] < 100:
    raise ValueError("Invalid disk reserve")
