import Header from "../components/Header";
import BayCard from "../components/BayCard";
import {getCameraArea, getVisibleBays} from "../config/cameras";
import {useCallback, useRef} from "react";
import {useNavigate} from "react-router-dom";
import {useAuth} from "../auth/AuthContext";
import {LayoutGrid} from "lucide-react";

// Preserve detection edges when returning from a bay so the same ongoing
// detection does not immediately pull the operator off the dashboard again.
const cameraVisibility = new Map();
const openedDetections = new Set();

export default function Dashboard() {
  const navigate = useNavigate();
  const {user} = useAuth();
  const bays = getVisibleBays(user.license_id);
  const area = getCameraArea(user.license_id);
  const opening = useRef(false);
  const onDetection = useCallback((bay, cameraId, data) => {
    if (document.hidden) return;
    const bayKey = `${user.license_id}:${bay.id}`;
    const cameraKey = `${user.license_id}:${cameraId}`;
    const visible = data.camera_connected === true && data.cavity_detected === true;
    cameraVisibility.set(cameraKey, visible);
    const bothClear = bay.hooks.every((camera) => cameraVisibility.get(`${user.license_id}:${camera.id}`) === false);
    if (bothClear) openedDetections.delete(bayKey);
    if (!visible || openedDetections.has(bayKey) || opening.current) return;
    opening.current = true;
    openedDetections.add(bayKey);
    navigate(`/bay/${bay.id}`);
  }, [navigate, user.license_id]);
  return (
    <>
      <Header />
      {area && <div className="bay-area-heading" id="active-bay-area">
        <LayoutGrid size={16} aria-hidden="true" />
        <h2>{area.name}</h2>
        <span>Bays {bays[0].id.slice(-2)}–{bays.at(-1).id.slice(-2)}</span>
      </div>}
      <main className="dashboard-grid" data-bay-count={bays.length}
        aria-labelledby={area ? "active-bay-area" : undefined} aria-label={area ? undefined : "All bays"}>
        {bays.map((bay) => (
          <BayCard
            key={bay.id}
            bay={bay}
            onDetection={onDetection}
          />
        ))}
      </main>
    </>
  );
}
