import {useEffect, useRef} from "react";
import "./HookLugGapIndicator.css";

/** gapPercent is a measured remaining gap (0–100), not a readiness score.
 * Engagement is exclusively controlled by the verified isEngaged flag. */
export default function HookLugGapIndicator({gapPercent = null, meterPercent = null, isEngaged = false, measuredOnly = false, label = "Hook–lug gap"}) {
  const engaged = isEngaged === true;
  const measured = typeof gapPercent === "number" && Number.isFinite(gapPercent);
  const target = engaged && !measuredOnly ? 0 : measured ? Math.max(0, Math.min(100, gapPercent)) : null;
  const hasMeter = Number.isFinite(meterPercent) && meterPercent >= 0 && meterPercent <= 100;
  const state = engaged ? "engaged" : target === null ? "unavailable" : target <= 40 ? "approaching" : "separated";
  const status = {engaged: "Engaged", unavailable: "NO GAP DATA", approaching: "Closing Gap", separated: "Not Engaged"}[state];
  const badge = engaged ? "ENGAGED" : target === null ? "NO DATA" : "GAP OPEN";
  const valueNode = useRef(null);
  const displayed = useRef(target);
  const initialText = useRef(target === null ? "—" : target.toFixed(1));

  useEffect(() => {
    let frame;
    const node = valueNode.current;
    const paint = (value) => {
      displayed.current = value;
      if (node) node.textContent = value === null ? "—" : value.toFixed(1);
    };
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (target === null || displayed.current === null || engaged || reducedMotion.matches) {
      paint(target);
      return undefined;
    }
    const from = displayed.current;
    if (from === target) return undefined;
    const start = performance.now();
    const tick = (now) => {
      const progress = Math.min(1, (now - start) / 260);
      paint(from + (target - from) * (1 - (1 - progress) ** 3));
      if (progress < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    const stopMotion = () => {
      if (reducedMotion.matches) { cancelAnimationFrame(frame); paint(target); }
    };
    reducedMotion.addEventListener("change", stopMotion);
    return () => { cancelAnimationFrame(frame); reducedMotion.removeEventListener("change", stopMotion); };
  }, [target, engaged]);

  const fill = target === null ? 0 : (100 - target) / 100;
  const accessibleValue = hasMeter ? `${meterPercent}% detector score. ${status}`
    : target === null ? "Gap measurement unavailable" : `${target.toFixed(1)}% remaining gap. ${status}`;

  return (
    <div className="hook-lug-gap" data-state={state} role="img" aria-label={`${label}: ${accessibleValue}`}>
      <div className="hook-lug-gap__header" aria-hidden="true">
        <span className="hook-lug-gap__badge">{badge}</span>
      </div>
      <div className="hook-lug-gap__body">
        <div className="hook-lug-gap__assembly" aria-hidden="true">
          <svg className="hook-lug-gap__icon" viewBox="0 0 32 36" fill="none">
            <path d="M17 3v7c0 5 9 5 9 13a10 10 0 1 1-14-9" stroke="currentColor" strokeWidth="4.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <div className="hook-lug-gap__track">
            <span className="hook-lug-gap__segment hook-lug-gap__segment--top" style={{transform: `scaleY(${fill})`}} />
            <span className="hook-lug-gap__segment hook-lug-gap__segment--bottom" style={{transform: `scaleY(${fill})`}} />
          </div>
          <svg className="hook-lug-gap__icon" viewBox="0 0 32 34" fill="none">
            <path d="M7 25V14a9 9 0 0 1 18 0v11M12 25V14a4 4 0 0 1 8 0v11M3 25h26v6H3z" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
        <div className="hook-lug-gap__readout" aria-hidden="true">
          <span className="hook-lug-gap__caption">REMAINING GAP</span>
          <div className="hook-lug-gap__value">
            <span ref={valueNode} hidden={hasMeter}>{initialText.current}</span>
            {hasMeter && <span>{meterPercent}</span>}
            <span className="hook-lug-gap__unit">%</span>
          </div>
          <span className="hook-lug-gap__status">{status}</span>
        </div>
      </div>
    </div>
  );
}
