# NMDC Lifting Hook Detector

A real-time lifting hook and lifting lug detector for DEEPX DX-M1, designed for RTSP live streams. Runtime inference uses compiled `.dxnn` models and classifies the lifting hook position relative to the lifting lug.

## Features

- DEEPX inference using four compiled `.dxnn` models
- Automatically blacks out left/right scene areas outside the detected lifting lug and hook in the preview
- Detects cavity, hook, and lock classes
- Smooths small lock-box shifts in the preview across consecutive detections; lift readiness still uses the current frame's lock detection
- Marks the hook ready immediately when cavity detection is present and the hook and lock boxes overlap; stays ready until the lock is no longer detected
- This vision-based `lift_ready` trigger is not a certified lifting safety interlock and should not be the sole authorization to lift
- Real-time  stream processing
- Overlay boxes in `blue` for lifting lug and `green` for lifting hook
- Alert text colors:
  - `yellow`: lifting hook inside the lug
  - `red`: lifting hook outside the lug
  - `white`: lifting hook or lug not detected
- Text-to-speech audio alerts for important states
- Works with video files and live camera streams as fallbacks

## Classification States

The detector reports whether the hook center is inside or outside the detected lifting-lug box. Missing-hook and missing-lug states remain distinct, and lock engagement is reported separately for lift readiness. Inside/outside status does not by itself authorize a lift.

Voice alerts are spoken in Hindi. The detector accepts both the legacy model labels (`container`, `hook`) and the names `lifting lug`, `lifting hook`; the two object classes remain distinct internally.

## Project Architecture

- `hook_detector/config.py` stores model path, RTSP URL, thresholds, and colors
- `hook_detector/classifier.py` classifies whether the hook is inside or outside the lifting lug
- `hook_detector/deepx_backend.py` runs the compiled DXNN inference pipeline
- `hook_detector/tts.py` speaks alarms and notifications
- `hook_detector/stream.py` manages RTSP/video/camera capture and visualization
- `app.py` is the main runtime entry point
- `train.py` is provided to train a new YOLO model on lifting-hook/lifting-lug data

## Step-by-step Process

1. Gather labeled video or image data showing a lifting lug and a separate lifting hook.
2. Label the classes as `container` (lifting lug) and `hook` (lifting hook) to match the current model interface.
3. Create a YOLO dataset structure and set up `data.yaml`.
4. Fine-tune both bundled model checkpoints separately on the same dataset:

   ```powershell
   python train.py --data data.yaml --epochs 100 --img 640
   ```

   By default, the training script uses both `weights/nmdc-yolo.pt` and
   `weights/nmdchook.pt`. It trains each checkpoint in its own run and prints
   the absolute path to each resulting `best.pt` file. Outputs are saved under
   `runs/train/hook_detector_nmdc-yolo/` and
   `runs/train/hook_detector_nmdchook/` (Ultralytics may add a suffix if a run
   directory already exists).

   To train a specific checkpoint or supply a different list, pass one or more
   paths explicitly:

   ```powershell
   python train.py --data data.yaml --weights weights/nmdc-yolo.pt weights/nmdchook.pt
   ```

4. The application runtime requires these four compiled models:

   ```text
   weights/nmdc-yolo.dxnn
   weights/nmdchook.dxnn
   weights/exp.dxnn
   weights/new_lock 1.dxnn
   ```

   The app loads only these `.dxnn` files for detection. `.pt` checkpoints may be used by the separate training/export workflow, but are not loaded by the runtime. See [START_HERE.md](START_HERE.md) for model export and compilation instructions.
5. Create the first Admin account once. The command prompts for a License ID and password; passwords must be at least 12 characters.

   ```bash
   python app.py --bootstrap-admin
   ```

6. Run the app:

   ```bash
   python app.py --host 127.0.0.1 --port 8001
   ```

7. Open `http://127.0.0.1:8002/docs` to inspect and test the FastAPI endpoints. The backend routes use port 8001; the API server and its interactive docs use port 8002. Configure cameras through the backend camera-admin routes documented below.

## Environment Setup

1. Create a virtual environment.
2. Install dependencies:

   pip install -r requirements.txt

3. Download the Hindi Piper voice:

   ```powershell
   python -m piper.download_voices --download-dir "$env:LOCALAPPDATA\NMDC_HookDetector\voices" hi_IN-rohan-medium
   ```

   The app uses this male voice by default for Hindi alerts. Set `PIPER_VOICE_PATH` to a different Hindi `.onnx` model path to use another voice.

4. Confirm the DEEPX DX-M1 device, driver, and DX-RT runtime are available.

## Linux Desktop Launch

From the project folder, install the desktop launcher and its dependencies:

```bash
sh install_linux.sh
```

The installer adds **NMDC Lifting Hook Detector** to the applications menu and creates `nmdc-hook-detector.desktop` in the configured Linux Desktop folder. Before the first launch, create an Admin with `./.venv-linux/bin/python app.py --bootstrap-admin`. Then search for the app or double-click the desktop icon. A terminal window stays open while the detector runs; closing it stops the app. The browser opens the interactive API docs automatically. The usual address is `http://localhost:8002/docs`; if either backend port is occupied, the launcher selects another available pair.

On Linux, installation downloads the Hindi Piper voice model and the backend speaks detection alerts through the system's default audio output. To route Piper audio directly to a PulseAudio or PipeWire sink, set `export NMDC_HOOK_AUDIO_SINK="bluez_output.AF:6A:EA:15:50:F6"` in `~/.config/nmdc-hook-detector/environment`. Set `export NMDC_HOOK_PORT="8001"` to choose a preferred backend port.

## Backend Camera Configuration

Use `POST /camera-admin/config` to configure the default camera or `POST /camera-admin/config/<camera-id>` for a specific camera. RTSP settings accept a hostname/IP, username, and password; optional `rtsp_port` and `rtsp_path` fields support camera-specific stream profiles. If omitted, the current Axis defaults are retained. USB settings accept a device index. Optional `poe_switch_port` stores an operator-assigned switch port. `GET /camera-admin/config/<camera-id>` returns public settings without the password. `POST` with `{"action":"reset"}` or `DELETE /camera-admin/config/<camera-id>` removes saved settings and stops that feed. `GET /camera-admin/usb-devices` lists available USB cameras.

An Admin can trigger `POST /camera-admin/discover` with the session cookie and `X-CSRF-Token` header. It sends a bounded ONVIF WS-Discovery multicast probe on the local link and returns `{"protocol":"onvif-ws-discovery","devices":[...]}`. Each result includes `ip`, advertised `manufacturer` and `model` when available, `onvif_endpoint`, and `poe_switch_port` (currently `null`). This is vendor-neutral for ONVIF cameras; cameras that do not advertise ONVIF must be entered manually. Discovery does not scan arbitrary IP ranges or ports.

Camera discovery cannot determine a PoE switch port by itself. Automatic port mapping requires a managed switch with an available read-only neighbor/MAC-table API or SNMPv3 integration; until that is configured, the frontend can save the assigned port manually as `poe_switch_port`.

Camera settings require an Admin session. Camera passwords are encrypted before they are saved and are not returned by the settings API. The encryption key is stored alongside the settings in the app data directory. The detector shares the loaded model across camera feeds and serializes inference, so throughput is shared across configured feeds. Configured feeds reconnect after restart.

## Account Authentication

Accounts are local to this installation; License ID is an account name, not a paid entitlement. Password hashes and sessions are stored in `auth.sqlite3` under the app data directory. Passwords are never stored or returned in plaintext. Create the first Admin from the detector host with `python app.py --bootstrap-admin`. After that, only an authenticated Admin can create or deactivate accounts.

If an account password is forgotten, reset it from the detector host with `python app.py --reset-password <license-id>`. Enter and confirm a new password at the hidden terminal prompts. This changes only that account and revokes its active sessions; camera settings and other accounts are preserved. Run it as the same operating-system user and with the same `HOOK_DETECTOR_DATA_DIR` environment as the backend.

The frontend login sends `POST /api/auth/login` with `{"license_id":"...","password":"..."}`. On success, the backend sets an HttpOnly session cookie and returns `user` (`license_id`, `role`) plus a `csrf_token`. Use `GET /api/auth/me` to restore the current login after a page refresh, `POST /api/auth/logout` with the `X-CSRF-Token` header to log out, and `GET /api/auth/licenses`, `POST /api/auth/licenses`, or `DELETE /api/auth/licenses/<license-id>` for Admin account management. New account bodies use `license_id`, `password`, and `role` (`admin` or `viewer`).

Send requests with cookies enabled (`credentials: "include"`) and keep the returned CSRF token in frontend memory for state-changing requests. Viewers can read detector status, camera health, streams, snapshots, and recordings. Admins can also configure/reset cameras, manage accounts, and shut down the backend. The backend enforces these roles on both ports; frontend-only route hiding is not authorization.

For local HTTP development, set `NMDC_COOKIE_SECURE=0`. The default is secure cookies, which require HTTPS in deployment. If the separately hosted frontend uses another origin, set `NMDC_ALLOWED_ORIGINS` to its exact origin (comma-separated for multiple origins). Prefer a same-origin HTTPS reverse proxy in production. Cookie-authenticated write requests require the returned `X-CSRF-Token` header.

The app stores its data in `.nmdc-hook-detector` under the running account's home directory. Set `HOOK_DETECTOR_DATA_DIR` to a writable directory to choose a different location (for example, a persistent volume in a container). Keep backups of this directory secure. This file-based setup is OS-independent and does not rely on a particular operating system's credential vault.

The detector host must be able to reach each camera's RTSP address. Allow RTSP on the camera/network (normally TCP port 554). The detector opens each RTSP stream with FFmpeg over TCP and bounded connection/read timeouts. Camera status and health are available at `/camera/<camera-id>/status` and `/camera/<camera-id>/health`, where IDs use the form `Bay-01-Camera-1`.

## Detection video archive

When either the lifting lug or hook is detected, the app records an annotated MP4 until both objects are no longer detected. Completed clips are stored on the detector computer in `~/.nmdc-hook-detector/recordings` (or `<HOOK_DETECTOR_DATA_DIR>/recordings` when that environment variable is set). The app prints the active folder when it starts.

The archive keeps the latest 10 completed clips across all cameras. When another clip completes, the oldest one is deleted. In-progress clips are finalized when a camera stops; incomplete files left by an abrupt shutdown are removed at the next startup. These recordings are local files.

**Deployment security:** the built-in services do not terminate TLS. Put them behind an HTTPS reverse proxy and restrict network access; do not expose the HTTP ports directly to the public internet.

## Backend API and Testing

The backend starts its camera and stream routes on the configured port and FastAPI on the next port. For example, with `--port 8001`, open `http://127.0.0.1:8002/docs` for the interactive API test page. The `/docs` page covers these API endpoints:

- GET /api/health — returns API liveness (`ok`) separately from camera connectivity, with `camera_connected`, `frame_ready`, `camera_source`, `camera_error`, and `retrying`
- GET /api/status — returns detector state plus camera connectivity/source/error/retry status. `state_key` is a stable machine-readable trigger; `hook_engaged` and `lift_ready` retain their existing boolean behavior. `hook_engagement_percent` and `lock_engagement_percent` return separate visual progress percentages based on detection and the distance between hook and lock bounding boxes. `meter` follows the lock engagement percentage, beginning at 10 when the lock alone is detected and increasing smoothly as the hook approaches. These values are visual progress indicators, not a certified lifting safety signal. `cavity_detected`, `hook_detected`, `lock_detected`, and `alert_message_hi` are also included.
- GET /api/trains/1/status and `/api/trains/2/status` — returns each configured camera's live status grouped into the four bays assigned to that train. Train 1 covers Bay-01 through Bay-04; Train 2 covers Bay-05 through Bay-08. Readiness remains independent per camera.
- GET /api/performance — returns CPU total/per-core/process use, system/process memory, available CPU temperature, DEEPX NPU utilization by device/core, inference latency/FPS, and up to 180 recent samples for charts. Temperature is `null` when the operating system exposes no supported CPU sensor; the response includes the sensor label when available.
- GET /api/engagement/summary — returns Today, Yesterday, This Week, and This Month engagement totals plus counts by bay. Optional `start_date` and `end_date` use `YYYY-MM-DD`; `timezone` accepts an IANA zone such as `Asia/Kolkata`; use either `bay_id` or `train_id=1|2` to filter.
- GET /api/engagement/calendar — returns daily engagement totals and a day-by-day count list for each bay. Defaults to the latest 15 days; `days` can be 1-31. Optional dates and `timezone` apply; use either `bay_id` or `train_id=1|2` to filter.
- GET /api/engagement/events — returns engagement start/end and state-change rows, defaulting to the latest 15 days. Send the same `start_date` and `end_date` to load a selected day. Use exactly one of `camera_id`, `bay_id`, or `train_id=1|2` to filter; a bay or train query returns events from the selected cameras. `limit` is capped at 500.
- GET /api/frame — returns the latest processed frame as image/jpeg (503 with an explanatory error if no frame is available)
- POST /api/shutdown — requests the detector to stop (204 No Content)
- POST /api/auth/login, GET /api/auth/me, POST /api/auth/logout — sign in, restore, and end a session
- GET/POST/DELETE /api/auth/licenses — Admin-only account listing, creation, and deactivation
- GET /api/charging/analysis — returns the Charging Analysis dashboard totals, period cards, bay comparison, and outcome breakdown. Supports `start_date`, `end_date`, `timezone`, `bay_id`, or `train_id=1|2`; `detected_lifts` is derived from engagement starts in the event archive. `missed` and outcome recording will be populated when missed-engagement events are available in the archive schema.

Camera unavailability does not mark the API itself unhealthy. RTSP and webcam streams automatically retry after an open/read failure. `/health`, `/status`, and per-camera status routes report the current camera error and retry state. Camera URLs and credentials are not included in API responses.

Engagement totals count transitions from `lift_ready=false` to `lift_ready=true`, not repeated frames or state changes during one continuous engagement. Video retention defaults to 15 days; event history is stored separately and defaults to 365 days via `event_retention_days` in `deployment.json`.

External clients can use the MJPEG `/stream` and `/camera/<camera-id>/stream` endpoints, or request still images from `/snapshot.jpg` and `/camera/<camera-id>/snapshot.jpg`. Camera preview publication runs independently of inference and publishes at up to 10 frames per second, resized to at most 1920 pixels wide. The encoded preview is reused by stream and snapshot endpoints.

The Python backend does not serve the product frontend. The separate frontend application can connect to the backend routes and APIs described here.

## Notes

- Runtime inference requires `weights/nmdc-yolo.dxnn`, `weights/nmdchook.dxnn`, `weights/exp.dxnn`, and `weights/new_lock 1.dxnn`.
- Preview boxes use blue for cavity, green for hook, and yellow for lock.

## License

This project is intended for research and industrial inspection tasks. Modify as needed for your deployment environment.
