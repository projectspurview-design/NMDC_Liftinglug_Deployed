import React, {useState} from "react";
import {createRoot} from "react-dom/client";
import HookLugGapIndicator from "../components/HookLugGapIndicator";
import "../index.css";
import "../interactions.css";

// Standalone developer preview. Never imported by the production application,
// never polls cameras, and never reports these values as detector measurements.
export default function HookLugGapIndicatorDemo() {
  const [gap, setGap] = useState(36.7);
  const [engaged, setEngaged] = useState(false);
  const [available, setAvailable] = useState(true);
  return (
    <main style={{minHeight: "100dvh", display: "grid", placeItems: "center", padding: 24}}>
      <section style={{display: "grid", gap: 24, justifyItems: "center", width: "min(100%, 340px)"}}>
        <h1 style={{fontSize: 15, color: "#fbbf24", margin: 0}}>DEMO · SIMULATED DATA</h1>
        <HookLugGapIndicator gapPercent={available ? gap : null} isEngaged={engaged} label="Demo hook–lug gap" />
        <div style={{display: "grid", gap: 16, width: "100%", fontSize: 13}}>
          <label style={{display: "grid", gap: 8}}>Demo gap percentage
            <input aria-label="Demo gap percentage" type="range" min="0" max="100" step="0.1" value={gap} onChange={(event) => setGap(Number(event.target.value))} />
          </label>
          <label><input type="checkbox" checked={engaged} onChange={(event) => setEngaged(event.target.checked)} /> Demo engagement flag</label>
          <label><input type="checkbox" checked={available} onChange={(event) => setAvailable(event.target.checked)} /> Demo gap data available</label>
        </div>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<React.StrictMode><HookLugGapIndicatorDemo /></React.StrictMode>);
