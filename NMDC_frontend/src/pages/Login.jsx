import {useState} from "react";
import {Navigate} from "react-router-dom";
import {Eye, EyeOff, LockKeyhole} from "lucide-react";
import {useAuth} from "../auth/AuthContext";
import logo from "../assets/canvas.png";
import "./Login.css";

const embers = [
  {x: 24, drift: -9, rise: 22, delay: 0},
  {x: 49, drift: 7, rise: 36, delay: -.8},
  {x: 62, drift: 12, rise: 25, delay: -2.4},
  {x: 78, drift: -6, rise: 32, delay: -1.6},
];

export default function Login() {
  const {user, loading, setupRequired, error: serviceError, login, refresh} = useAuth();
  const [licenseId, setLicenseId] = useState("");
  const [password, setPassword] = useState("");
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (loading) return <div className="auth-loading" role="status">Loading login…</div>;
  if (user) return <Navigate to="/" replace />;
  async function submit(event) {
    event.preventDefault(); setBusy(true); setError("");
    try { await login(licenseId.trim(), password); }
    catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }
  return (
    <main className="login-page">
      <aside className="login-mine">
        <div className="login-mine-landscape" aria-hidden="true">
          <div className="login-mine-rails" />
          <div className="login-mine-train-track">
            <div className="login-mine-rolling-stock">
              <img className="login-mine-engine" src="/login-coke-train.webp" alt="" />
              <img className="login-mine-underframe" src="/login-coke-train.webp" alt="" />
              <span className="login-mine-headlight" />
            </div>
            <div className="login-mine-container">
              <img src="/login-coke-train.webp" alt="" />
              <div className="login-coke-heat">
                {embers.map(({x, drift, rise, delay}) => (
                  <span key={x} style={{"--ember-x": `${x}%`, "--ember-drift": `${drift}px`,
                    "--ember-rise": `-${rise}px`, "--ember-delay": `${delay}s`}} />
                ))}
              </div>
              <span className="login-mine-lug login-mine-rear" />
              <span className="login-mine-lug login-mine-front" />
            </div>
            <div className="login-mine-hoist">
              <span className="login-mine-rear">
                <span className="login-mine-cable" />
                <span className="login-mine-hook" />
              </span>
              <span className="login-mine-front">
                <span className="login-mine-cable" />
                <span className="login-mine-hook" />
              </span>
            </div>
          </div>
        </div>
        <div className="login-mine-content">
          <img src={logo} alt="NMDC" className="login-logo" />
          <p className="login-mine-title">Lifting hook<br />safety monitoring</p>
          <p className="login-mine-subtitle">Coke handling and camera monitoring</p>
        </div>
      </aside>
      <section className="login-card">
        <div className="login-symbol" aria-hidden="true"><LockKeyhole size={22} /></div>
        <h1>{setupRequired ? "Create administrator license" : "Sign in"}</h1>
        <p>{setupRequired ? "Set up the first admin license to manage cameras and create other logins." : "Enter your license ID and password to access camera monitoring."}</p>
        <form onSubmit={submit} className="account-form">
          <label>License ID<input autoComplete="username" required maxLength={64} value={licenseId} onChange={(event) => setLicenseId(event.target.value)} placeholder="Enter license ID" /></label>
          <label>Password
            <div className="password-field">
              <input type={visible ? "text" : "password"} autoComplete={setupRequired ? "new-password" : "current-password"} required minLength={setupRequired ? 10 : undefined} maxLength={256} value={password} onChange={(event) => setPassword(event.target.value)} placeholder={setupRequired ? "At least 10 characters" : "Enter password"} />
              <button type="button" aria-label={visible ? "Hide password" : "Show password"} onClick={() => setVisible(!visible)}>{visible ? <EyeOff size={18} /> : <Eye size={18} />}</button>
            </div>
          </label>
          {(error || serviceError) && <p className="form-error" role="alert">{error || serviceError}</p>}
          <button className="primary-action" disabled={busy || Boolean(serviceError)}>{busy ? "Please wait…" : setupRequired ? "Create admin and log in" : "Log in"}</button>
          {serviceError && <button type="button" onClick={refresh}>Retry connection</button>}
        </form>
        {setupRequired && <p className="login-note">License IDs use letters, numbers, hyphens or underscores.</p>}
      </section>
    </main>
  );
}
