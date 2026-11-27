// Visual language shared by every panel.
//
// The Hook Detection backend owns the state machine and sends the state name plus its
// severity ("details" in the payload). Colors live here because hex colors are
// a browser concern -- the Python side keeps BGR tuples for OpenCV overlays.
export const STATE_COLORS = {
  HOOK_ENGAGED_TO_LOCK: "#4ade80",
  HOOK_NOT_ENGAGED_TO_LOCK: "#f87171",
  NO_HOOK_DETECTED: "#8e9aa4",
  CAVITY_NOT_DETECTED: "#8e9aa4",
  HOOK_NOT_ENGAGED: "#e24b4a",
  HOOK_DISENGAGED: "#e24b4a",
  EXACT_CENTER: "#22c55e",
  CENTER: "#22c55e",
  CORRECTLY_ALIGNED: "#22c55e",
  FAR_LEFT: "#e24b4a",
  FAR_RIGHT: "#e24b4a",
  TOUCHING_LEFT: "#e24b4a",
  TOUCHING_RIGHT: "#e24b4a",
  OUT_LEFT: "#b91c1c",
  OUT_RIGHT: "#b91c1c",
  EXTREME_LEFT: "#e24b4a",
  EXTREME_RIGHT: "#e24b4a",
  LEFT: "#ef9f27",
  RIGHT: "#ef9f27",
  CORRECT_READY_TO_LIFT: "#22c55e",
};

export const SEVERITY_COLORS = {
  idle: "#8e9aa4",
  ready: "#22c55e",
  warning: "#ef9f27",
  unsafe: "#e24b4a",
  Low: "#22c55e",
  Medium: "#ef9f27",
  High: "#e24b4a",
};

export const stateColor = (state, fallback = "#8e9aa4") => {
  const key = typeof state === "string" ? state.trim().replace(/[\s-]+/g, "_").toUpperCase() : "";
  if (STATE_COLORS[key]) return STATE_COLORS[key];
  if (/(^|_)(OUT|OUTSIDE|EXTREME|FAR|DISENGAGED|NOT_ENGAGED|TOUCHING)(_|$)/.test(key)) {
    return key.includes("OUT") ? "#b91c1c" : "#e24b4a";
  }
  if (/(^|_)(LEFT|RIGHT)(_|$)/.test(key)) return "#ef9f27";
  if (/(^|_)(CENTER|CENTERED|ALIGNED|READY_TO_LIFT)(_|$)/.test(key)) return "#22c55e";
  return fallback;
};
export const severityColor = (severity) => SEVERITY_COLORS[severity] || "#8e9aa4";

export const panel = {
  background: "rgba(255,255,255,.08)",
  border: "1px solid rgba(255,255,255,.12)",
  borderRadius: 20,
  padding: 20,
};

export const eyebrow = {
  fontSize: 11,
  letterSpacing: ".14em",
  textTransform: "uppercase",
  opacity: 0.7,
};

const OFFLINE = {title: "Connecting…", severity: "idle", color: STATE_COLORS.NO_HOOK_DETECTED};

/** Title/severity/color for a Hook Detection payload, with sane placeholders. */
export function describeState(data) {
  if (!data) return OFFLINE;
  const details = data.details?.[data.state] || {};
  const severity = data.severity || details.severity || OFFLINE.severity;
  return {
    title: data.state_title || details.title || data.state || OFFLINE.title,
    severity,
    color: stateColor(data.state, severityColor(severity)),
  };
}

export function formatOffset(offset) {
  if (typeof offset === "number" && Number.isFinite(offset)) {
    return `${offset > 0 ? "+" : ""}${offset.toFixed(1)} px`;
  }
  return typeof offset === "string" && offset !== "-" ? offset : "--";
}

export function formatTime(ts) {
  return typeof ts === "number" ? new Date(ts * 1000).toLocaleTimeString() : "--";
}
