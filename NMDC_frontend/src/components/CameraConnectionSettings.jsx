import {useEffect, useState} from "react";
import {Eye, EyeOff} from "lucide-react";
import {normalizeBase} from "../services/api";
import {csrfHeaders, remoteBackend} from "../api/axios";

const fieldStyle = {
  width: "100%",
  boxSizing: "border-box",
  marginTop: 5,
  padding: "8px 10px",
  borderRadius: 8,
  border: "1px solid rgba(148,163,184,.4)",
  background: "rgba(15,23,42,.7)",
  color: "inherit",
};

async function request(url, body, method = body === undefined ? "GET" : "POST") {
  const response = await fetch(url, {
    method,
    credentials: "same-origin",
    cache: "no-store",
    headers: body === undefined ? {} : {"Content-Type": "application/json", ...(remoteBackend ? csrfHeaders() : {})},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const contentType = response.headers.get("content-type") || "";
  if (response.status === 404 && remoteBackend) {
    throw new Error("USB camera configuration is unavailable on the remote detector.");
  }
  if (response.status !== 204 && !contentType.includes("application/json")) {
    throw new Error(
      "Camera API returned a non-JSON response. Start or restart the detector with the updated app, then reload this page.",
    );
  }
  const result = response.status === 204 ? {} : await response.json();
  if (!response.ok) {
    if (
      method === "POST" &&
      body?.action === "reset" &&
      response.status === 400 &&
      result.error === "Camera address, username, and password must be text."
    ) {
      throw new Error("This detector has not loaded camera reset support yet. Restart it with the updated app, then try again.");
    }
    throw new Error(result.error || result.detail || `Request failed (${response.status}).`);
  }
  return result;
}

export default function CameraConnectionSettings({camera, showHeading = true, usbOnly = false}) {
  const [form, setForm] = useState({
    source_type: usbOnly ? "usb" : "rtsp",
    device_index: 0,
    host: "",
    username: "",
    password: "",
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [needsReconfiguration, setNeedsReconfiguration] = useState(false);
  const [usbDevices, setUsbDevices] = useState([]);
  const [scanningUsb, setScanningUsb] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const cameraAdminEndpoint = `${normalizeBase(camera.url)}/camera-admin`;
  const endpoint = `${cameraAdminEndpoint}/config/${encodeURIComponent(camera.id)}`;

  function setAvailableUsbDevices(devices) {
    const availableDevices = Array.isArray(devices) ? devices : [];
    setUsbDevices(availableDevices);
    setForm((previous) => ({
      ...previous,
      device_index: availableDevices.some(
        (device) => device.device_index === Number(previous.device_index),
      )
        ? Number(previous.device_index)
        : availableDevices[0]?.device_index ?? previous.device_index,
    }));
  }

  async function scanUsbCameras() {
    setScanningUsb(true);
    setError("");
    try {
      const result = await request(`${cameraAdminEndpoint}/usb-devices`);
      setAvailableUsbDevices(result.devices);
    } catch (scanError) {
      setError(scanError.message);
    } finally {
      setScanningUsb(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    request(endpoint)
      .then((result) => {
        if (cancelled) return;
        setConfigured(Boolean(result.camera));
        setNeedsReconfiguration(Boolean(result.needs_reconfiguration));
        setForm((previous) => ({
          ...previous,
          source_type: usbOnly ? "usb" : result.camera?.source_type || "rtsp",
          device_index: result.camera?.device_index ?? 0,
          host: result.camera?.host || "",
          username: result.camera?.username || "",
          password: "",
        }));
      })
      .catch((loadError) => {
        if (!cancelled) setError(loadError.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [endpoint, usbOnly]);

  useEffect(() => {
    if (form.source_type !== "usb") return undefined;
    let cancelled = false;
    setScanningUsb(true);
    setError("");
    request(`${cameraAdminEndpoint}/usb-devices`)
      .then((result) => {
        if (!cancelled) setAvailableUsbDevices(result.devices);
      })
      .catch((scanError) => {
        if (!cancelled) setError(scanError.message);
      })
      .finally(() => {
        if (!cancelled) setScanningUsb(false);
      });
    return () => {
      cancelled = true;
    };
  }, [cameraAdminEndpoint, form.source_type]);

  const update = (key) => (event) => {
    setForm((previous) => ({...previous, [key]: event.target.value}));
    setMessage("");
  };

  async function saveCamera(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const payload = form.source_type === "usb"
        ? {source_type: "usb", device_index: Number(form.device_index)}
        : {
            source_type: "rtsp",
            host: form.host,
            username: form.username,
            password: form.password,
          };
      const result = await request(endpoint, payload);
      setConfigured(Boolean(result.saved));
      setNeedsReconfiguration(false);
      setShowPassword(false);
      setForm((previous) => ({...previous, password: ""}));
      setMessage(form.source_type === "usb"
        ? `USB camera ${form.device_index} selected. This camera is connecting.`
        : "Settings saved. This camera is connecting.");
    } catch (saveError) {
      setError(saveError.message);
    } finally {
      setSaving(false);
    }
  }

  async function resetCamera() {
    if (!window.confirm(`Reset ${camera.name} and stop its stream? You will need to configure the camera again to reconnect.`)) {
      return;
    }
    setSaving(true);
    setError("");
    setMessage("");
    try {
      await request(endpoint, {action: "reset"});
      setConfigured(false);
      setNeedsReconfiguration(false);
      setShowPassword(false);
      setForm({source_type: "rtsp", device_index: 0, host: "", username: "", password: ""});
      setMessage("Camera settings reset. The stream is stopped until you configure the camera and save.");
    } catch (resetError) {
      setError(resetError.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      className="camera-connection-form"
      onSubmit={saveCamera}
      style={{
        marginTop: 12,
        padding: 12,
        borderRadius: 10,
        background: "rgba(2,6,23,.24)",
        border: "1px solid rgba(255,255,255,.1)",
      }}
    >
      {showHeading && (
        <>
          <strong style={{fontSize: 13}}>Camera connection settings</strong>
          <p style={{margin: "5px 0 10px", fontSize: 11, lineHeight: 1.45, opacity: 0.72}}>
            Choose RTSP for a network camera or USB for a camera connected to this computer.
          </p>
        </>
      )}
      {needsReconfiguration && (
        <p role="status" style={{margin: "0 0 10px", color: "#fbbf24", fontSize: 12}}>
          Re-enter the camera details to migrate the previously saved settings.
        </p>
      )}

      <div style={{display: "grid", gridTemplateColumns: "1fr", gap: 8}}>
        {!usbOnly && <label style={{fontSize: 12}}>
          Camera connection type
          <select
            value={form.source_type}
            onChange={update("source_type")}
            style={fieldStyle}
          >
            <option value="rtsp">Network camera (RTSP)</option>
            <option value="usb">USB camera</option>
          </select>
        </label>}

        {form.source_type === "usb" ? (
          <div>
            <label style={{fontSize: 12}}>
              Available USB cameras
              <select
                value={form.device_index}
                onChange={(event) => setForm((previous) => ({
                  ...previous,
                  device_index: Number(event.target.value),
                }))}
                disabled={loading || scanningUsb || usbDevices.length === 0}
                style={fieldStyle}
              >
                {usbDevices.length === 0 && (
                  <option value={form.device_index}>
                    {scanningUsb ? "Searching for USB cameras…" : "No USB cameras found"}
                  </option>
                )}
                {usbDevices.map((device) => (
                  <option key={device.device_index} value={device.device_index}>
                    {device.name} (index {device.device_index})
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="camera-rescan-button"
              onClick={scanUsbCameras}
              disabled={loading || scanningUsb || saving}
              style={{marginTop: 7, padding: "6px 9px", borderRadius: 7}}
            >
              {scanningUsb ? "Searching…" : "Rescan USB cameras"}
            </button>
            <span style={{display: "block", marginTop: 5, opacity: 0.7, lineHeight: 1.4}}>
              Cameras recognized by the detector computer are listed here, even if they are not currently streaming video.
            </span>
          </div>
        ) : (
          <>
            <label style={{fontSize: 12}}>
              Camera IP address or hostname
              <input
                autoComplete="off"
                required
                value={form.host}
                onChange={update("host")}
                style={fieldStyle}
              />
            </label>
            <label style={{fontSize: 12}}>
              Camera username
              <input
                autoComplete="username"
                required
                value={form.username}
                onChange={update("username")}
                style={fieldStyle}
              />
            </label>
            <label style={{fontSize: 12}}>
              Camera password{configured ? " (re-enter to save changes)" : ""}
              <span style={{position: "relative", display: "block"}}>
                <input
                  type={showPassword ? "text" : "password"}
                  autoComplete="new-password"
                  required
                  value={form.password}
                  onChange={update("password")}
                  style={{...fieldStyle, paddingRight: 42}}
                />
                <button
                  type="button"
                  className="camera-password-toggle"
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  aria-pressed={showPassword}
                  title={showPassword ? "Hide password" : "Show password"}
                  onClick={() => setShowPassword((visible) => !visible)}
                  style={{
                    position: "absolute",
                    top: "50%",
                    right: 6,
                    transform: "translateY(-50%)",
                    display: "grid",
                    placeItems: "center",
                    minWidth: 32,
                    minHeight: 32,
                    padding: 6,
                    border: 0,
                    borderRadius: 6,
                    background: "transparent",
                    color: "rgba(226,232,240,.8)",
                    cursor: "pointer",
                  }}
                >
                  {showPassword ? <EyeOff size={18} aria-hidden="true" /> : <Eye size={18} aria-hidden="true" />}
                </button>
              </span>
            </label>
          </>
        )}
      </div>

      <button
        type="submit"
        className="camera-connect-button"
        disabled={loading || saving || (form.source_type === "usb" && usbDevices.length === 0)}
        style={{
          marginTop: 10,
          padding: "8px 12px",
          borderRadius: 8,
          border: "1px solid rgba(147,197,253,.45)",
          background: "#1d4ed8",
          color: "white",
          cursor: loading || saving ? "wait" : "pointer",
        }}
      >
        {loading ? "Loading settings…" : saving ? "Saving…" : "Save and connect"}
      </button>

      {configured && !usbOnly && (
        <button
          type="button"
          disabled={loading || saving}
          className="camera-reset-button"
          onClick={resetCamera}
          style={{
            display: "block",
            marginTop: 8,
            padding: "8px 12px",
            borderRadius: 8,
            border: "1px solid rgba(248,113,113,.55)",
            background: "rgba(127,29,29,.65)",
            color: "white",
            cursor: loading || saving ? "wait" : "pointer",
          }}
        >
          {saving ? "Please wait…" : "Reset camera and stop stream"}
        </button>
      )}

      {error && <p role="alert" style={{margin: "8px 0 0", color: "#fca5a5", fontSize: 12}}>{error}</p>}
      {message && <p role="status" style={{margin: "8px 0 0", color: "#86efac", fontSize: 12}}>{message}</p>}
    </form>
  );
}
