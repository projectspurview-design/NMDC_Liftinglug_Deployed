import {useId} from "react";
import "./HookDetectionIndicator.css";

export default function HookDetectionIndicator({isDetected, cameraConnected = false}) {
  const gradient = `hook-metal-${useId().replace(/:/g, "")}`;
  const state = !cameraConnected ? "offline"
    : isDetected === true ? "detected"
    : isDetected === false ? "searching" : "waiting";
  const text = {offline: "CAMERA OFFLINE", detected: "HOOK DETECTED", searching: "SEARCHING…", waiting: "WAITING…"}[state];

  return (
    <div className="hook-detection-indicator" data-state={state} role="status" aria-live="polite" aria-label={text}>
      <div className="hook-detection-indicator__visual" aria-hidden="true">
        <svg viewBox="0 0 56 64" fill="none">
          <defs>
            <linearGradient id={gradient} x1="14" y1="8" x2="43" y2="53" gradientUnits="userSpaceOnUse">
              <stop stopColor="#edf7ff" />
              <stop offset=".42" stopColor="#738da2" />
              <stop offset=".75" stopColor="#bacbda" />
              <stop offset="1" stopColor="#e2e8f0" />
            </linearGradient>
          </defs>
          <path d="M15 4H9a5 5 0 0 0-5 5v10m37-15h6a5 5 0 0 1 5 5v10M4 45v10a5 5 0 0 0 5 5h6m26 0h6a5 5 0 0 0 5-5V45" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          <path d="M25 11h8v15c9 2 14 8 14 16a17 17 0 0 1-34 0c0-4 2-8 4-10l6 8a9 9 0 1 0 10-6l-8-4V11Z" fill={`url(#${gradient})`} stroke="#d5e6f1" strokeWidth="1" strokeLinejoin="round" />
          <rect x="25" y="8" width="9" height="7" rx="2" fill={`url(#${gradient})`} />
        </svg>
        {state === "searching" && <span className="hook-detection-indicator__scan" />}
        {state === "detected" && <span className="hook-detection-indicator__check">✓</span>}
      </div>
      <span className="hook-detection-indicator__label" aria-hidden="true">{text}</span>
    </div>
  );
}
