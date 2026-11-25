from pathlib import Path
import os
from urllib.parse import quote

BASE_DIR = Path(__file__).resolve().parent.parent
WEIGHTS_DIR = BASE_DIR / "weights"

MODEL_PATHS = [
    str(WEIGHTS_DIR / "nmdc-yolo.dxnn"),
    str(WEIGHTS_DIR / "nmdchook.dxnn"),
    str(WEIGHTS_DIR / "exp.dxnn"),
    str(WEIGHTS_DIR / "new_lock 1.dxnn"),
]
MODEL_PATH = MODEL_PATHS[0]


def _axis_rtsp_url() -> str | None:
    camera_ip = os.getenv("AXIS_CAMERA_IP")
    if not camera_ip:
        return None

    username = quote(os.getenv("AXIS_CAMERA_USERNAME", ""), safe="")
    password = quote(os.getenv("AXIS_CAMERA_PASSWORD", ""), safe="")
    credentials = f"{username}:{password}@" if username else ""
    return f"rtsp://{credentials}{camera_ip}/axis-media/media.amp?streamprofile=Profile_1"


DEFAULT_RTSP_URL = (
    os.getenv("RTSP_URL")
    or _axis_rtsp_url()
    or None
)

CONTAINER_CLASS = "container"
HOOK_CLASS = "hook"
LOCK_CLASS = "lock"

# OpenCV BGR colors
BLUE = (255, 0, 0)
GREEN = (0, 255, 0)
RED = (0, 0, 255)
YELLOW = (0, 255, 255)
WHITE = (255, 255, 255)

STATE_HOOK_INSIDE_LUG = "HOOK_INSIDE_LUG"
STATE_HOOK_OUTSIDE_LUG = "HOOK_OUTSIDE_LUG"
STATE_HOOK_NOT_ENGAGED = "HOOK_NOT_ENGAGED"
STATE_GAP_MISSING = "CAVITY_NOT_DETECTED"
STATE_HOOK_LOCK_ENGAGED = "HOOK_ENGAGED_TO_LOCK"
STATE_HOOK_LOCK_NOT_ENGAGED = "HOOK_NOT_ENGAGED_TO_LOCK"
STATE_HOOK_LOCK_CONTACT_NOT_READY = "HOOK_LOCK_CONTACT_NOT_READY"
CONFIDENCE_THRESHOLD = 0.25
TTS_LUG_VISIBLE_DELAY_SECONDS = 3.0
TTS_LUG_VISIBLE_REPEAT_COUNT = 2
TTS_LOCATION_STABLE_DELAY_SECONDS = 4.0
TTS_LOCATION_ALERT_INTERVAL_SECONDS = 5.0

# Hindi status messages used by the app and voice alerts
STATUS_TEXT = {
    STATE_HOOK_INSIDE_LUG: "लिफ्टिंग हुक लिफ्टिंग लग के अंदर है।",
    STATE_HOOK_OUTSIDE_LUG: "चेतावनी। लिफ्टिंग हुक लिफ्टिंग लग के बाहर है। लिफ्ट न करें।",
    STATE_HOOK_NOT_ENGAGED: "सावधान। लिफ्टिंग हुक लगा नहीं है। लिफ्ट न करें।",
    STATE_GAP_MISSING: "सावधान। लिफ्टिंग लग दिखाई नहीं दे रहा है। लिफ्ट न करें।",
    STATE_HOOK_LOCK_ENGAGED: "हुक लॉक से जुड़ा हुआ है और लिफ्ट करने के लिए तैयार है।",
    STATE_HOOK_LOCK_NOT_ENGAGED: "चेतावनी। हुक लॉक से जुड़ा नहीं है। लिफ्ट न करें।",
    STATE_HOOK_LOCK_CONTACT_NOT_READY: "हुक लॉक को छू रहा है, लेकिन लिफ्ट करने के लिए तैयार नहीं है।",
}

LIFTING_LUG_VISIBLE_TEXT = "लिफ्टिंग लग सही स्थिति में है। लिफ्टिंग हुक जोड़ने के लिए तैयार है।"

DISPLAY_STATUS_TEXT = {
    STATE_HOOK_INSIDE_LUG: "Lifting hook inside lifting lug",
    STATE_HOOK_OUTSIDE_LUG: "Lifting hook outside lifting lug",
    STATE_HOOK_NOT_ENGAGED: "Lifting hook not engaged",
    STATE_GAP_MISSING: "Lifting lug not detected",
}
