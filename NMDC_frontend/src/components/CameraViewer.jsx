import {useAuth} from "../auth/AuthContext";
import LiveCameraImage from "./LiveCameraImage";
import {useEffect, useRef, useState} from "react";
import {CameraOff, Download, Maximize2, Minimize2, Radio, ZoomIn, ZoomOut} from "lucide-react";
import useDetectorFeed from "../hooks/useDetectorFeed";
import {eyebrow, panel, stateColor} from "../theme";
import RefreshButton from "./RefreshButton";
import HookLugGapIndicator from "./HookLugGapIndicator";
import HookDetectionIndicator from "./HookDetectionIndicator";
import {HOOK_DETECTION_API} from "../config/cameras";

export default function CameraViewer({camera, onData}) {
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({x: 0, y: 0});
  const [dragging, setDragging] = useState(false);
  const drag = useRef(null);
  const viewport = useRef(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [viewError, setViewError] = useState("");
  const [savingFrame, setSavingFrame] = useState(false);
  const [actionNote, setActionNote] = useState("");
  const noteTimer = useRef(null);
  const {aiToken, setAiToken} = useAuth();
  const {data, connected, transport} = useDetectorFeed(camera.endpoint);
  // The unscoped FastAPI status reports the primary detector only.
  const primary = camera.id === "Bay-01-Camera-1";
  const {data: primaryStatus} = useDetectorFeed(primary ? HOOK_DETECTION_API : null, {statusPath: "/api/status"});
  const endpoint = data?.api_url || camera.endpoint;
  const offline = Boolean(data) && !data.camera_connected;
  const streamConnected = Boolean(data?.camera_connected);
  const matchingPrimaryStatus = primaryStatus?.camera_connected &&
    (!primaryStatus.camera_id || primaryStatus.camera_id === camera.id) ? primaryStatus : null;
  const engagementFlag = matchingPrimaryStatus?.hook_engaged ?? matchingPrimaryStatus?.lift_ready ??
    data?.hook_engaged ?? data?.lift_ready;
  const gapPercent = primary ? matchingPrimaryStatus?.gap_percent : data?.gap_percent;
  const detectorScore = (primary ? matchingPrimaryStatus : data)?.meter;
  const engaged = streamConnected && engagementFlag === true;
  const connectionLabel = streamConnected ? "Online" : offline ? "Camera offline" : "Connecting";
  const connectionColor = streamConnected ? "#22c55e" : offline ? "#f59e0b" : "#94a3b8";
  const alertText = data?.voice_alert || data?.alert_main || data?.status || data?.state_title || data?.state;
  const alertColor = stateColor(data?.state, data?.severity === "High" ? "#ef4444" : undefined);
  const alignmentCommand = (primary ? matchingPrimaryStatus?.hook_lock_command : null) ||
    data?.hook_lock_command;
  const alignmentKey = data?.alignment_state || "-";
  const alignmentText = !streamConnected ? "Not visible" : alignmentCommand || ({
    EXACT_CENTER: "Centre", CENTER: "Centre", LEFT: "Left", RIGHT: "Right",
    EXTREME_LEFT: "Extreme left", EXTREME_RIGHT: "Extreme right",
    OUT_LEFT: "Extreme left", OUT_RIGHT: "Extreme right",
  }[alignmentKey] || "Not visible");
  const alignmentState = alignmentCommand
    ? (primary ? matchingPrimaryStatus?.hook_lock_state : null) || data?.hook_lock_state
    : alignmentKey;
  const alignmentColor = streamConnected ? stateColor(alignmentState) : "#cbd5e1";
  const panelGlow = !streamConnected ? "none"
    : data?.cavity_detected === false ? "orange"
    : engaged ? "green"
    : data?.cavity_detected === true && engagementFlag === false ? "red"
    : "none";

  useEffect(() => {
    onData?.(data, connected, transport, camera.id);
  }, [camera.id, data, connected, onData, transport]);

  useEffect(() => {
    setZoom(1);
    setPan({x: 0, y: 0});
    drag.current = null;
    setDragging(false);
  }, [camera.id]);
  useEffect(() => () => clearTimeout(noteTimer.current), []);

  const canPan = streamConnected && zoom > 1;
  const clampPan = (value) => Math.max(-1, Math.min(1, value));
  const resetView = () => { setZoom(1); setPan({x: 0, y: 0}); };
  useEffect(() => {
    const update = () => setFullscreen(document.fullscreenElement === viewport.current);
    document.addEventListener("fullscreenchange", update);
    return () => document.removeEventListener("fullscreenchange", update);
  }, []);
  async function toggleFullscreen() {
    setViewError("");
    try {
      if (document.fullscreenElement === viewport.current) await document.exitFullscreen();
      else if (viewport.current?.requestFullscreen) await viewport.current.requestFullscreen();
      else setViewError("Fullscreen is unavailable in this browser.");
    } catch { setViewError("Fullscreen could not open. Try again from this browser window."); }
  }
  async function saveFrame() {
    setViewError("");
    setActionNote("");
    const image = viewport.current?.querySelector("img");
    if (!streamConnected || !image?.complete || !image.naturalWidth) {
      setViewError("Wait for a camera frame before saving.");
      return;
    }
    setSavingFrame(true);
    try {
      // Copy the already-decoded frame: no new camera request or inference.
      const canvas = document.createElement("canvas");
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      canvas.getContext("2d").drawImage(image, 0, 0);
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", .92));
      if (!blob) throw new Error("Frame encoding failed");
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${camera.id}-${new Date().toISOString().replace(/[:.]/g, "-")}.jpg`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setActionNote("Frame download started");
      clearTimeout(noteTimer.current);
      noteTimer.current = setTimeout(() => setActionNote(""), 3000);
    } catch { setViewError("The frame could not be saved. Try again when the camera is showing a frame."); }
    finally { setSavingFrame(false); }
  }
  function startPan(event) {
    if (event.target.closest("button, input, select, a")) return;
    if (!canPan || !event.isPrimary || event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = {id: event.pointerId, x: event.clientX, y: event.clientY, pan};
    setDragging(true);
  }
  function movePan(event) {
    const start = drag.current;
    if (!canPan || !start || start.id !== event.pointerId) return;
    const maxX = event.currentTarget.clientWidth * (zoom - 1) / 2;
    const maxY = event.currentTarget.clientHeight * (zoom - 1) / 2;
    if (maxX <= 0 || maxY <= 0) return;
    setPan({
      x: clampPan(start.pan.x + (event.clientX - start.x) / maxX),
      y: clampPan(start.pan.y + (event.clientY - start.y) / maxY),
    });
  }
  function endPan(event) {
    if (drag.current?.id !== event.pointerId) return;
    drag.current = null;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }
  function panWithKeys(event) {
    if (!canPan) return;
    const directions = {ArrowLeft: [-.1, 0], ArrowRight: [.1, 0], ArrowUp: [0, -.1], ArrowDown: [0, .1]};
    if (event.key === "Home") { event.preventDefault(); setPan({x: 0, y: 0}); return; }
    const direction = directions[event.key];
    if (!direction) return;
    event.preventDefault();
    setPan((position) => ({x: clampPan(position.x + direction[0]), y: clampPan(position.y + direction[1])}));
  }

  return (
    <div className="camera-viewer" style={{...panel, padding: 14}}>
      <div className="camera-viewer-heading">
        <div>
          <h2>{camera.name || "Camera feed"}</h2>
          <span style={eyebrow}>Hook and lifting lug detection</span>
        </div>
        <div className="camera-viewer-actions">
          <div className="camera-zoom" role="group" aria-label={`${camera.name} zoom controls`}>
            <button type="button" aria-label={`${camera.name} zoom out`} title="Zoom out" disabled={!streamConnected || zoom <= 1} onClick={() => setZoom((value) => Math.max(1, value - .25))}><ZoomOut size={16} /></button>
            <button type="button" className="camera-zoom__reset" aria-label={`${camera.name} reset zoom`} title="Reset zoom and position" disabled={!streamConnected || zoom === 1} onClick={resetView}>{zoom.toFixed(2)}×</button>
            <button type="button" aria-label={`${camera.name} zoom in`} title="Zoom in" disabled={!streamConnected || zoom >= 4} onClick={() => setZoom((value) => Math.min(4, value + .25))}><ZoomIn size={16} /></button>
          </div>
          <span className="camera-connection-status" role="status">
            <span style={{background: connectionColor}} />
            {connectionLabel}
          </span>
          <RefreshButton />
          <button className="camera-fullscreen-button" type="button" aria-label={`${camera.name} save frame`} title="Save current camera frame" disabled={!streamConnected || savingFrame} onClick={saveFrame}><Download size={16} /></button>
          <button className="camera-fullscreen-button" type="button" aria-label={`${camera.name} fullscreen`} title="Open fullscreen" onClick={toggleFullscreen}><Maximize2 size={16} /></button>
        </div>
      </div>

      <div className="camera-monitor-stage">
      <div
        className="camera-viewer-viewport"
        ref={viewport}
        data-glow={panelGlow}
        data-pannable={canPan}
        data-dragging={dragging}
        data-stream-connected={streamConnected}
        role="region"
        aria-label={`${camera.name} video. Zoom in, then drag or use arrow keys to move the image. Home centers it.`}
        tabIndex={canPan ? 0 : undefined}
        onPointerDown={startPan}
        onPointerMove={movePan}
        onPointerUp={endPan}
        onPointerCancel={endPan}
        onLostPointerCapture={endPan}
        onKeyDown={panWithKeys}
        style={{
          position: "relative",
          aspectRatio: "16 / 9",
          width: "100%",
          display: "grid",
          placeItems: "center",
          background: "rgba(255,255,255,.06)",
          border: "1px solid rgba(255,255,255,.18)",
          borderRadius: 14,
          overflow: "hidden",
        }}
      >
        {data?.camera_connected ? (
          <>
            <LiveCameraImage
              key={aiToken ? "ai" : "standard"}
              endpoint={endpoint}
              aiToken={aiToken}
              onAiExpired={() => setAiToken(null)}
              alt="Live lifting hook and lifting lug detection feed"
              style={{
                position: "absolute",
                inset: 0,
                display: "block",
                width: "100%",
                height: "100%",
                objectFit: "cover",
                transform: `translate(${zoom > 1 ? pan.x * (zoom - 1) * 50 : 0}%, ${zoom > 1 ? pan.y * (zoom - 1) * 50 : 0}%) scale(${zoom})`,
                transformOrigin: "center center",
                transition: dragging ? "none" : "transform 180ms ease-out",
                pointerEvents: "none",
                background: "rgba(255,255,255,.06)",
              }}
            />

          </>
        ) : (
          <div className="camera-feed-placeholder">
            <div className="camera-feed-placeholder-icon">
              {offline ? <CameraOff size={21} aria-hidden="true" /> : <Radio size={21} aria-hidden="true" />}
            </div>
            <p>
              {data ? "Waiting for a camera frame…" : "Connecting to Lifting Hook Detection…"}
            </p>
            {offline && (
              <p className="camera-feed-placeholder-detail">
                {data.camera_error || "Camera is not connected."}
              </p>
            )}
          </div>
        )}
        <div className="camera-text-overlay">
          <div className="camera-alignment-text" role="status" aria-live="polite">
            <span>Hook alignment</span>
            <strong style={{color: alignmentColor}}>{alignmentText}</strong>
          </div>
          <div className="camera-alert-text" role="status" aria-live="polite">
            <span>Alert</span>
            <strong style={{color: alertColor}}>{alertText && alertText !== "-" ? alertText : "Waiting for camera data"}</strong>
          </div>
          <div className="camera-hook-detection">
            <HookDetectionIndicator isDetected={data?.hook_detected} cameraConnected={streamConnected} />
          </div>
        </div>
        <div className="camera-gap-compact" title="Hook-lug detector gauge" aria-label={`${camera.name} gap instrument`}>
          <HookLugGapIndicator
            gapPercent={streamConnected ? gapPercent : null}
            meterPercent={streamConnected ? detectorScore : null}
            measuredOnly={primary}
            isEngaged={engaged}
            label={`${camera.name} hook–lug gap`}
          />
        </div>
        {fullscreen && <button type="button" className="camera-fullscreen-exit" aria-label="Exit camera fullscreen" onClick={toggleFullscreen}><Minimize2 size={16} /> Exit fullscreen</button>}
        {actionNote && <span className="camera-action-note" role="status">{actionNote}</span>}
      </div>

      </div>
      {viewError && <p className="camera-view-error" role="alert">{viewError}</p>}

    </div>
  );
}
