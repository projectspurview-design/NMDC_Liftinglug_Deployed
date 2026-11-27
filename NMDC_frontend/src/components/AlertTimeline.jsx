import {describeState, eyebrow, formatOffset, formatTime, panel, stateColor} from "../theme";

export default function AlertTimeline({cameraName, data, history = []}) {
  const {title, color} = describeState(data);

  return (
    <div className="camera-alert-panel" style={panel}>
      <div style={eyebrow}>{cameraName ? `${cameraName} · Live alerts` : "Live alerts"}</div>

      <p className="camera-alert-message" style={{color}}>
        {data?.voice_alert || data?.alert_main || data?.status || "Waiting for Lifting Hook Detector data..."}
      </p>
      <p className="camera-alert-state">
        State: {data?.state ? `${title} (${data.state})` : "--"}
      </p>

      {history.length > 0 && (
        <>
          <div style={{...eyebrow, marginTop: 18}}>Recent state changes</div>
          <ul
            style={{
              listStyle: "none",
              margin: "8px 0 0",
              padding: 0,
              maxHeight: 240,
              overflowY: "auto",
            }}
          >
            {history.map((entry, index) => (
              <li
                key={`${entry.state}-${entry.ts}-${index}`}
                style={{
                  display: "flex",
                  alignItems: "baseline",
                  gap: 10,
                  padding: "8px 0",
                  borderTop: "1px solid rgba(255,255,255,.1)",
                }}
              >
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: stateColor(entry.state),
                    flex: "0 0 auto",
                  }}
                />
                <span style={{flex: 1, fontSize: 14, color: stateColor(entry.state)}}>
                  {entry.title || entry.state}
                </span>
                <span style={{opacity: 0.7, fontSize: 12}}>{formatOffset(entry.offset)}</span>
                <span style={{opacity: 0.55, fontSize: 12}}>{formatTime(entry.ts)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
