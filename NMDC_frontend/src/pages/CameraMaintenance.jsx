import {apiErrorMessage, remoteBackend} from "../api/axios";
import {useEffect, useRef, useState} from "react";
import {Camera, Plug, RefreshCw, Settings2, Usb, X} from "lucide-react";
import {useAuth} from "../auth/AuthContext";
import {bays, getVisibleBays, HOOK_DETECTION_API} from "../config/cameras";
import {getNetworkCameraAddresses, getPoeAssignments, removePoeAssignment, requestPoeAssignment} from "../services/api";
import {panel} from "../theme";
import CameraConnectionSettings from "../components/CameraConnectionSettings";

const emptyAssignment = {ip_address: "", switch_port: ""};
const CAMERA_SCAN_INTERVAL_MS = 10_000;
const cameraSlots = bays.flatMap((bay) => bay.hooks.map((camera) => ({bay, camera})));

function normalizeIp(value) {
  const address = value.trim();
  if (!address.includes(":")) return address;
  try {
    return new URL(`http://[${address}]`).hostname.slice(1, -1);
  } catch {
    return address;
  }
}

function AssignmentWarning({assignments, cameraId, fields, saving, onRestore}) {
  const ip = normalizeIp(fields.ip_address);
  const port = Number(fields.switch_port);
  if (!ip || !Number.isSafeInteger(port) || port < 1) return null;

  const saved = assignments[cameraId];
  const changed = saved && (saved.ip_address !== ip || saved.switch_port !== port);
  const conflicts = cameraSlots.filter(({camera}) => {
    const assignment = assignments[camera.id];
    return camera.id !== cameraId && assignment && (assignment.ip_address === ip || assignment.switch_port === port);
  });
  if (!changed && !conflicts.length) return null;

  return (
    <section className="poe-assignment-warning" aria-label="PoE assignment warning" aria-live="polite">
      <strong>Check the IP address and PoE port</strong>
      {changed && <p>Saved for this camera: <strong>{saved.ip_address}</strong>, port <strong>{saved.switch_port}</strong>.</p>}
      {conflicts.map(({bay, camera}) => {
        const assignment = assignments[camera.id];
        return <p key={camera.id}>{bay.id} · {camera.name} uses <strong>{assignment.ip_address}</strong>, port <strong>{assignment.switch_port}</strong>.</p>;
      })}
      {changed && <button type="button" disabled={saving} onClick={onRestore}>Use saved pair</button>}
    </section>
  );
}

function SavedMappings({assignments, unavailable, disabled, deletingId, deleteError, deleteMessage, onDelete}) {
  const rows = assignments ? cameraSlots.filter(({camera}) => assignments[camera.id]) : [];

  return (
    <section className="settings-bay-panel poe-saved-mappings" style={panel} aria-labelledby="saved-mappings-title">
      <h2 id="saved-mappings-title" style={{margin: 0}}>Saved IP and PoE port mappings</h2>
      {rows.length ? (
        <table className="poe-mappings-table" aria-labelledby="saved-mappings-title">
          <thead>
            <tr><th scope="col">Bay</th><th scope="col">Camera</th><th scope="col">IP address</th><th scope="col">PoE port</th><th scope="col">Action</th></tr>
          </thead>
          <tbody>
            {rows.map(({bay, camera}) => {
              const {ip_address, switch_port} = assignments[camera.id];
              return (
                <tr key={camera.id}>
                  <td>{bay.id}</td><td>{camera.name}</td><td>{ip_address}</td><td>{switch_port}</td>
                  <td>
                    <button type="button" className="poe-delete-button" disabled={disabled}
                      aria-label={`Delete mapping for ${bay.id} ${camera.name}`}
                      onClick={() => onDelete(bay, camera, assignments[camera.id])}>
                      {deletingId === camera.id ? "Deleting…" : "Delete"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : (
        <p className="poe-mappings-empty">{unavailable ? "Saved mappings are unavailable." : assignments ? "No mappings saved yet." : "Loading saved mappings…"}</p>
      )}
      {deleteError && <p className="form-error" role="alert">{deleteError}</p>}
      {deleteMessage && <p className="form-success" role="status">{deleteMessage}</p>}
    </section>
  );
}

function CameraAssignmentForm({camera, assignments, addresses, busy, saving, feedback, onSave, onUsb}) {
  const [draft, setDraft] = useState(null);
  const [manualIp, setManualIp] = useState(false);
  const [connectError, setConnectError] = useState("");
  const connectDialog = useRef(null);
  const cameraId = camera.id;
  const fields = draft ?? assignments?.[cameraId] ?? emptyAssignment;
  const selectedIp = fields.ip_address;
  const disabled = !assignments || busy;

  function edit(name, value) {
    setDraft((current) => ({...(current ?? fields), [name]: value}));
  }

  async function submit(event) {
    event.preventDefault();
    const connecting = event.nativeEvent.submitter?.value === "connect";
    const username = fields.username?.trim() ?? "";
    const password = fields.password ?? "";
    setConnectError("");
    if (connecting && !event.currentTarget.checkValidity()) {
      setConnectError("Select an IP address and enter a valid PoE switch port in the camera assignment.");
      return;
    }
    if (connecting && (!username || !password)) {
      setConnectError("Enter the camera username and password.");
      return;
    }
    const saved = await onSave(cameraId, {
      ip_address: selectedIp.trim(),
      switch_port: Number(fields.switch_port),
      ...(connecting && {username, password}),
    });
    if (saved) {
      setDraft(null);
      setManualIp(false);
      if (connecting) connectDialog.current?.close();
    }
  }

  return (
    <form className="account-form poe-camera-form" aria-labelledby={`${cameraId}-title`} onSubmit={submit}>
      <div className="poe-camera-heading">
        <h3 id={`${cameraId}-title`}><Camera size={15} aria-hidden="true" /> {camera.name}</h3>
        <div className="poe-camera-actions">
          <button className="workspace-action poe-camera-connect-trigger" type="button"
            aria-haspopup="dialog" disabled={busy} onClick={() => onUsb(camera)}>
            <Usb size={13} aria-hidden="true" /> USB camera
          </button>
        <button className="workspace-action poe-camera-connect-trigger" type="button" disabled={disabled || remoteBackend}
          title={remoteBackend ? "The remote API does not accept camera credentials." : undefined}
          aria-haspopup="dialog" onClick={() => connectDialog.current?.showModal()}>
          <Plug size={13} aria-hidden="true" /> Connect
        </button>
        </div>
      </div>
      <div className="poe-camera-fields">
        <label>
          <span className="sr-only">Camera IP address</span>
          <select name="ip_address_choice" value={manualIp ? "manual" : selectedIp}
            className="poe-ip-select" data-placeholder={!manualIp && !selectedIp}
            disabled={disabled} required
            onChange={(event) => {
              const value = event.target.value;
              setManualIp(value === "manual");
              if (value !== "manual") edit("ip_address", value);
            }}>
            <option value="" disabled>IP address</option>
            {addresses.map((address) => <option key={address} value={address}>{address}</option>)}
            {!manualIp && selectedIp && !addresses.includes(selectedIp) && <option value={selectedIp}>{selectedIp}</option>}
            <option value="manual">Enter IP address manually</option>
          </select>
        </label>
        <label>
          <span className="sr-only">PoE switch port</span>
          <input name="switch_port" type="number" min="1" step="1" placeholder="PoE switch" value={fields.switch_port}
            onChange={(event) => edit("switch_port", event.target.value)} disabled={disabled} required />
        </label>
        {manualIp && <label className="poe-manual-ip">Manual IP address
          <input name="ip_address" value={selectedIp} onChange={(event) => edit("ip_address", event.target.value)}
            disabled={disabled} required autoComplete="off" spellCheck={false} maxLength={45} />
        </label>}
        <button className="primary-action" type="submit" disabled={disabled}>
          {saving ? "Saving…" : "Save assignment"}
        </button>
      </div>
      <dialog ref={connectDialog} className="license-dialog poe-camera-connect" aria-labelledby={`${cameraId}-connect-title`}
        onClose={() => {
          setConnectError("");
          setDraft(current => {
            if (!current) return current;
            const {username, password, ...assignment} = current;
            return assignment;
          });
        }}>
        <h4 id={`${cameraId}-connect-title`}>Connect {camera.name.toLowerCase()}</h4>
        <div className="poe-camera-credentials" onKeyDown={event => {
          if (event.key === "Enter" && event.target.tagName === "INPUT") {
            event.preventDefault();
            event.currentTarget.querySelector('button[value="connect"]').click();
          }
        }}>
          <input aria-label="Camera username" placeholder="Camera username" autoComplete="off"
            value={fields.username ?? ""} maxLength={256} disabled={disabled}
            onChange={(event) => edit("username", event.target.value)} />
          <input aria-label="Camera password" placeholder="Camera password" type="password" autoComplete="new-password"
            value={fields.password ?? ""} maxLength={1024} disabled={disabled}
            onChange={(event) => edit("password", event.target.value)} />
          <div className="poe-connect-actions">
            <button className="workspace-action" type="button" onClick={() => connectDialog.current?.close()}>Cancel</button>
            <button className="workspace-action" type="submit" value="connect" formNoValidate disabled={disabled}>{saving ? "Connecting…" : "Connect"}</button>
          </div>
        </div>
        {connectError && <p className="form-error" role="alert">{connectError}</p>}
        {feedback?.connecting && feedback.error && <p className="form-error" role="alert">{feedback.error}</p>}
      </dialog>
      {assignments && <AssignmentWarning assignments={assignments} cameraId={cameraId} fields={fields}
        saving={busy} onRestore={() => { setDraft(null); setManualIp(false); }} />}
      {feedback?.error && !feedback.connecting && <p className="form-error" role="alert">{feedback.error}</p>}
      {feedback?.saved && !draft && <p className="form-success" role="status">{feedback.message}</p>}
    </form>
  );
}

export default function CameraMaintenance() {
  const [usbCamera, setUsbCamera] = useState(null);
  const usbDialog = useRef(null);
  useEffect(() => {
    if (usbCamera) usbDialog.current?.showModal();
  }, [usbCamera]);
  const {user} = useAuth();
  const visibleBays = getVisibleBays(user.license_id);
  const [assignments, setAssignments] = useState(null);
  const [savingId, setSavingId] = useState(null);
  const [saveFeedback, setSaveFeedback] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const [deleteError, setDeleteError] = useState("");
  const [deleteMessage, setDeleteMessage] = useState("");
  const [loadError, setLoadError] = useState("");
  const [addresses, setAddresses] = useState([]);
  const [scan, setScan] = useState(0);
  const [scanning, setScanning] = useState(!remoteBackend);
  const [scanError, setScanError] = useState("");
  const busy = Boolean(savingId || deletingId);

  useEffect(() => {
    const controller = new AbortController();
    let pending = false;
    let timer;

    async function scanCameras() {
      if (document.hidden || pending) return;
      pending = true;
      setScanning(true);
      try {
        const found = await getNetworkCameraAddresses(HOOK_DETECTION_API, controller.signal);
        if (!controller.signal.aborted) {
          setAddresses(found);
          setScanError("");
        }
      } catch (failure) {
        if (!controller.signal.aborted) setScanError(apiErrorMessage(failure, "Could not discover cameras."));
      } finally {
        pending = false;
        if (!controller.signal.aborted) {
          setScanning(false);
          if (!remoteBackend && !document.hidden) timer = setTimeout(scanCameras, CAMERA_SCAN_INTERVAL_MS);
        }
      }
    }

    function onVisibilityChange() {
      clearTimeout(timer);
      if (!document.hidden) scanCameras();
    }

    if (!remoteBackend || scan > 0) scanCameras();
    if (!remoteBackend) document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      controller.abort();
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [scan]);

  useEffect(() => {
    const controller = new AbortController();
    getPoeAssignments(HOOK_DETECTION_API, controller.signal)
      .then((saved) => {
        if (!controller.signal.aborted) {
          const ids = new Set(visibleBays.flatMap(bay => bay.hooks.map(camera => camera.id)));
          setAssignments(Object.fromEntries(Object.entries(saved).filter(([id]) => ids.has(id))));
        }
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setLoadError(apiErrorMessage(failure, "Could not load the PoE assignments."));
      });
    return () => controller.abort();
  }, [visibleBays]);

  function rescan() {
    setScanError("");
    setScanning(true);
    setScan((current) => current + 1);
  }

  async function save(cameraId, fields) {
    setSavingId(cameraId);
    setSaveFeedback(null);
    try {
      const assignment = await requestPoeAssignment(HOOK_DETECTION_API, cameraId, {
        assignment: fields,
      });
      setAssignments((previous) => ({...previous, [cameraId]: assignment}));
      setSaveFeedback({cameraId, saved: true, message: fields.username ? "Camera settings saved. Check the bay feed for connection status." : "Assignment saved."});
      return true;
    } catch (failure) {
      setSaveFeedback({cameraId, connecting: Boolean(fields.username), error: apiErrorMessage(failure, "Could not save the PoE assignment.")});
      return false;
    } finally {
      setSavingId(null);
    }
  }

  async function deleteMapping(targetBay, camera, assignment) {
    const description = `${targetBay.id} · ${camera.name}`;
    if (!window.confirm(`Delete the saved mapping for ${description}? ${assignment.ip_address}, PoE port ${assignment.switch_port}.`)) return;
    setDeletingId(camera.id);
    setDeleteError("");
    setDeleteMessage("");
    try {
      await removePoeAssignment(HOOK_DETECTION_API, camera.id, assignment);
      setAssignments((previous) => {
        const remaining = {...previous};
        delete remaining[camera.id];
        return remaining;
      });
      setSaveFeedback((previous) => previous?.cameraId === camera.id ? null : previous);
      setDeleteMessage(`Mapping deleted for ${description}.`);
    } catch (failure) {
      setDeleteError(apiErrorMessage(failure, "Could not delete the PoE assignment."));
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <>
      <section aria-labelledby="poe-assignment-title">
        <header className="workspace-heading">
          <h1 id="poe-assignment-title"><Settings2 size={28} aria-hidden="true" /> PoE Switch Port Assignments</h1>
          <div className="poe-scan-controls" data-scanning={scanning}>
            <span className="workspace-heading-status" role="status">{scanning ? "Discovering cameras…" : scanError ||
              (addresses.length ? `${addresses.length} camera IP ${addresses.length === 1 ? "address" : "addresses"} detected` :
                remoteBackend && scan === 0 ? "Ready to scan" : "No network cameras detected.")}</span>
            <button className="workspace-action" type="button" disabled={scanning || busy} onClick={rescan}>
              <RefreshCw size={15} aria-hidden="true" /> {remoteBackend ? "Scan cameras" : "Scan again"}
            </button>
          </div>
        </header>
        {loadError && <p className="form-error" role="alert">{loadError}</p>}
        <div className="poe-workspace">
          {visibleBays.map((bay) => (
            <section key={bay.id} className="poe-bay-editor" aria-labelledby={`${bay.id}-editor-title`}>
              <header className="poe-bay-row-title">
                <h2 id={`${bay.id}-editor-title`}>{bay.id.replace("-", " ")}</h2>
                {assignments && <span>{bay.hooks.filter(({id}) => assignments[id]).length} / {bay.hooks.length} mapped</span>}
              </header>
              <div className="poe-camera-pair">
                {bay.hooks.map((camera) => {
                  const saved = assignments?.[camera.id];
                  return (
                    <CameraAssignmentForm
                      key={`${camera.id}:${saved?.ip_address ?? ""}:${saved?.switch_port ?? ""}`}
                      camera={camera} assignments={assignments} addresses={addresses}
                      busy={busy} saving={savingId === camera.id}
                      feedback={saveFeedback?.cameraId === camera.id ? saveFeedback : null} onSave={save} onUsb={setUsbCamera}
                    />
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      </section>
      <SavedMappings assignments={assignments} unavailable={Boolean(loadError && !assignments)}
        disabled={!assignments || busy} deletingId={deletingId} deleteError={deleteError}
        deleteMessage={deleteMessage} onDelete={deleteMapping} />
      <dialog ref={usbDialog} className="license-dialog" onClose={() => setUsbCamera(null)} aria-labelledby="usb-camera-title">
        <div className="license-dialog-heading">
          <h2 id="usb-camera-title">Connect USB camera</h2>
          <button type="button" aria-label="Close USB camera settings" onClick={() => usbDialog.current?.close()}><X size={19} /></button>
        </div>
        {usbCamera && <>
          <p>{usbCamera.id.replace(/-Camera-[12]$/, "").replace("-", " ")} · {usbCamera.name}</p>
          <CameraConnectionSettings key={usbCamera.id} camera={usbCamera} usbOnly showHeading={false} />
        </>}
      </dialog>
    </>
  );
}
