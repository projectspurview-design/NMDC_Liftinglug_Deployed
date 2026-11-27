import {CameraOff, Radio} from "lucide-react";
import LiveCameraImage from "./LiveCameraImage";
import {useEffect, useState} from "react";
import {Link} from "react-router-dom";
import useDetectorFeed from "../hooks/useDetectorFeed";

function CameraPreview({camera, bay, onDetection}) {
  const {data, connected} = useDetectorFeed(camera.endpoint, {pollMs: 3000});
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (data) onDetection?.(bay, camera.id, data);
  }, [bay, camera.id, data, onDetection]);

  const live = connected && !failed;
  return (
    <div className="bay-card-camera">
      <div className="bay-card-camera-heading" style={{display: "flex", justifyContent: "space-between", gap: 8, fontSize: 12, marginBottom: 6}}>
        <span title={camera.name}>{camera.side}</span>
        <span className="tile-connection-label" title={live ? "Live" : data ? "Offline" : "Connecting"} style={{color: live ? "#22c55e" : "#d39a45"}}>
          {live ? "Live" : data ? "Offline" : "Connecting"}
        </span>
      </div>
      <div className="bay-card-viewport" style={{position: "relative", aspectRatio: "16 / 9", overflow: "hidden", borderRadius: 10, background: "#101820", display: "grid", placeItems: "center"}}>
        {connected && (
          <LiveCameraImage
            endpoint={camera.endpoint}
            alt={`${camera.name} live preview`}
            onLoad={() => setFailed(false)}
            onError={() => setFailed(true)}
            style={{width: "100%", height: "100%", objectFit: "cover", position: "absolute", visibility: failed ? "hidden" : "visible"}}
          />
        )}
        {(!connected || failed) && (
          <div className="bay-card-placeholder" title={data?.camera_error || "Open bay to configure camera"} style={{textAlign: "center", padding: 16, fontSize: 12, opacity: 0.75}}>
            {data ? <CameraOff size={22} aria-hidden="true" /> : <Radio size={22} aria-hidden="true" />}
            <p style={{margin: "0 0 6px"}}>{data ? "Camera view unavailable" : "Connecting to camera…"}</p>
            <span>{failed ? "Retrying preview…" : data?.camera_error || "Open bay to configure camera"}</span>
          </div>
        )}
      </div>
    </div>
  );
}

export default function BayCard({bay, onDetection}) {
  return (
    <section className="bay-camera-pair" aria-label={bay.id}>
      <h2 className="bay-pair-label">{bay.id}</h2>
      {bay.hooks.map((camera) => (
    <Link
      key={camera.id}
      className="camera-tile"
      to={`/bay/${bay.id}`}
      aria-label={`Open ${bay.id} ${camera.side} camera monitor`}
    >
      <CameraPreview camera={camera} bay={bay} onDetection={onDetection} />
    </Link>
      ))}
    </section>
  );
}
