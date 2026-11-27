import {useEffect, useId, useRef, useState} from "react";
import {useLocation} from "react-router-dom";
import {Check, CircleHelp, Copy, Download, Maximize2, Move, RotateCcw, X, ZoomIn} from "lucide-react";

export default function MonitorUtilities() {
  const location = useLocation();
  const dialog = useRef(null);
  const timer = useRef(null);
  const titleId = useId();
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState("");
  useEffect(() => {
    setCopied(false);
    setCopyError("");
    clearTimeout(timer.current);
    return () => clearTimeout(timer.current);
  }, [location.pathname]);

  async function copyLink() {
    setCopyError("");
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 3000);
    } catch { setCopyError("Copy the monitor URL from your address bar."); }
  }

  return (
    <div className="monitor-utilities">
      {location.pathname.startsWith("/bay/") && <button type="button" className="utility-button"
        aria-label={copied ? "Monitor link copied" : "Copy monitor link"} title={copied ? "Link copied" : "Copy monitor link"}
        onClick={copyLink}>{copied ? <Check size={16} /> : <Copy size={16} />}</button>}
      <button type="button" className="utility-button" aria-label="Camera controls guide" title="Camera controls guide" onClick={() => dialog.current?.showModal()}><CircleHelp size={18} /></button>
      {copyError && <span className="utility-message" role="status">{copyError}</span>}
      {copied && <span className="sr-only" role="status">Monitor link copied. The recipient will need to log in.</span>}
      <dialog ref={dialog} className="controls-guide" aria-labelledby={titleId}>
        <div className="controls-guide__heading"><h2 id={titleId}>Camera controls</h2><button type="button" className="utility-button" aria-label="Close controls guide" onClick={() => dialog.current?.close()}><X size={18} /></button></div>
        <dl>
          <div><ZoomIn size={18} /><dt>Zoom</dt><dd>Use the camera’s + and − controls for 1×–4× zoom.</dd></div>
          <div><Move size={18} /><dt>Move the image</dt><dd>Zoom in, then drag with your mouse or finger. Focus the video and use arrow keys to pan.</dd></div>
          <div><RotateCcw size={18} /><dt>Recenter</dt><dd><kbd>Home</kbd> centers the zoomed image. The magnification button resets zoom and position.</dd></div>
          <div><Maximize2 size={18} /><dt>Fullscreen</dt><dd>Expand a camera using its fullscreen button. Press <kbd>Esc</kbd> or use Exit fullscreen to return.</dd></div>
          <div><Download size={18} /><dt>Save a frame</dt><dd>Download the current camera frame as a JPEG. It includes the camera image and detector boxes, without interface overlays or digital zoom.</dd></div>
        </dl>
      </dialog>
    </div>
  );
}
