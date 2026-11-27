import {useRef, useState} from "react";
import {apiErrorMessage, verifyPassword} from "../api/axios";
import {Link, NavLink} from "react-router-dom";
import {Activity, ChartNoAxesColumnIncreasing, LayoutGrid, Settings2} from "lucide-react";
import logo from "../assets/canvas.png";
import ProfileMenu from "./ProfileMenu";
import {useAuth} from "../auth/AuthContext";
import MonitorUtilities from "./MonitorUtilities";

export default function Header() {
  const {user, aiToken, setAiToken} = useAuth();
  const aiDialog = useRef(null);
  const [aiError, setAiError] = useState("");
  const [aiPending, setAiPending] = useState(false);
  function toggleAi() {
    if (aiToken) {
      setAiToken(null);
      return;
    }
    setAiError("");
    aiDialog.current.showModal();
  }
  async function unlockAi(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const password = new FormData(form).get("password");
    setAiError("");
    setAiPending(true);
    try {
      await verifyPassword(password, {timeout: 4000});
      if (aiDialog.current?.open) {
        setAiToken(true);
        aiDialog.current.close();
      }
    } catch (error) {
      setAiError(apiErrorMessage(error, "AI highlights could not be loaded."));
    } finally {
      form.reset();
      setAiPending(false);
    }
  }
  return (
    <header className="app-header">
      <div className="app-header-brand">
        <Link to="/" className="app-header-home" aria-label="NMDC home: Bays">
          <img src={logo} alt="" className="app-header-logo" />
        </Link>
        <div className="app-header-copy">
          <h1 className="app-header-title">Intelligent Hook–Bucket Lug Safety <span>Engagement Monitoring</span></h1>
        </div>
      </div>
      <nav className="app-header-nav" aria-label="Main navigation">
        <NavLink to="/" end><LayoutGrid size={16} aria-hidden="true" /> Bays</NavLink>
        <button className="utility-button header-ai-button" type="button" aria-pressed={Boolean(aiToken)} title="Toggle AI detection highlights" onClick={toggleAi}>AI</button>
        {user?.role === "admin" && <NavLink to="/settings"><Settings2 size={16} aria-hidden="true" /> Camera settings</NavLink>}
        {user?.role === "admin" && <NavLink to="/performance"><Activity size={16} aria-hidden="true" /> System Performance</NavLink>}
        {user?.role === "admin" && <NavLink to="/engagements"><ChartNoAxesColumnIncreasing size={16} aria-hidden="true" /> Charging Analysis</NavLink>}
        <MonitorUtilities />
        <ProfileMenu />
      </nav>
      <dialog className="camera-ai-dialog" ref={aiDialog} aria-label="Unlock AI highlights" onClose={(event) => event.currentTarget.querySelector("form").reset()}>
        <form onSubmit={unlockAi}>
          <label>
            Developer password
            <input name="password" type="password" required autoFocus autoComplete="off" disabled={aiPending} />
          </label>
          {aiError && <p role="alert">{aiError}</p>}
          <div className="camera-ai-dialog-actions">
            <button type="button" onClick={() => aiDialog.current.close()}>Cancel</button>
            <button type="submit" disabled={aiPending}>{aiPending ? "Unlocking…" : "Unlock AI"}</button>
          </div>
        </form>
      </dialog>
    </header>
  );
}
