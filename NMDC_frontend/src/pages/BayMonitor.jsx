import {Link, Navigate, useParams} from "react-router-dom";
import {ChevronLeft} from "lucide-react";
import CameraViewer from "../components/CameraViewer";
import Header from "../components/Header";
import {useAuth} from "../auth/AuthContext";
import {bays, getVisibleBays} from "../config/cameras";

export default function BayMonitor() {
  const {id} = useParams();
  const {user} = useAuth();
  const bay = bays.find((entry) => entry.id === id);
  if (bay && !getVisibleBays(user.license_id).includes(bay)) return <Navigate to="/" replace />;
  if (!bay) {
    return (
      <>
        <Header />
        <div style={{padding: 30}}>
          <h1>Bay not found</h1>
          <p style={{opacity: 0.75}}>No bay is configured with the id “{id}”.</p>
          <Link to="/" style={{color: "inherit"}}>← Back to all bays</Link>
        </div>
      </>
    );
  }

  return (
    <>
      <Header />
      <main className="bay-monitor-page">
        <div className="bay-monitor-context">
          <Link to="/"><ChevronLeft size={16} aria-hidden="true" /> Bays</Link>
          <h1>{bay.id.replace("-", " ")}</h1>
        </div>
        <div className="bay-monitor-grid">
          {bay.hooks.map((camera) => (
            <section key={camera.id} className="bay-monitor-camera" aria-label={camera.name}>
              <CameraViewer camera={camera} />
            </section>
          ))}
        </div>
      </main>
    </>
  );
}
