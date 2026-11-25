from typing import Dict, Optional, Tuple

from .config import (
    DISPLAY_STATUS_TEXT,
    RED,
    STATE_GAP_MISSING,
    STATE_HOOK_INSIDE_LUG,
    STATE_HOOK_NOT_ENGAGED,
    STATE_HOOK_OUTSIDE_LUG,
    WHITE,
    YELLOW,
)

def classify_hook_position(
    container_box: Optional[Dict[str, float]],
    hook_box: Optional[Dict[str, float]],
) -> Tuple[str, tuple[int, int, int], str]:
    """Return (status_text, box_color, state_key)."""
    if container_box is None:
        return DISPLAY_STATUS_TEXT[STATE_GAP_MISSING], WHITE, STATE_GAP_MISSING

    if hook_box is None:
        return DISPLAY_STATUS_TEXT[STATE_HOOK_NOT_ENGAGED], WHITE, STATE_HOOK_NOT_ENGAGED

    hook_x1, hook_y1, hook_x2, hook_y2 = hook_box["xyxy"]
    container_x1, container_y1, container_x2, container_y2 = container_box["xyxy"]
    is_outside = (
        hook_x2 < container_x1
        or hook_x1 > container_x2
        or hook_y2 < container_y1
        or hook_y1 > container_y2
    )
    state = STATE_HOOK_OUTSIDE_LUG if is_outside else STATE_HOOK_INSIDE_LUG
    color = RED if is_outside else YELLOW
    return DISPLAY_STATUS_TEXT[state], color, state
