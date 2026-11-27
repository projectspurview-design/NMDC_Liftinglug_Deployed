import {describeState, eyebrow, formatTime, panel, stateColor} from "../theme";

export default function StatusBadge({data}) {
  const {title, color} = describeState(data);
  const alignmentColor = stateColor(data?.state, color);

  return (
    <div className="alignment-panel" style={panel}>
      <div style={eyebrow}>Current alignment</div>
      <div className="alignment-panel-title" style={{color: alignmentColor}}>{title}</div>
      <div className="alignment-panel-meta">
        <span>State <strong style={{color: alignmentColor}}>{data?.state || "--"}</strong></span>
        <span>Updated <strong>{data?.updated || formatTime(data?.ts)}</strong></span>
      </div>
    </div>
  );
}
