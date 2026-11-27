import {apiClient as axios, csrfHeaders, remoteBackend} from "../api/axios";
import {bays} from "../config/cameras";

// Hook Detection API URLs may carry a trailing slash.
export const normalizeBase = (url) => String(url ?? "").trim().replace(/\/+$/, "");

const TIMEOUT_MS = 4000;
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
const dateKey = date => date.toISOString().slice(0, 10);
const bayKey = value => /^bay[\s_-]*0?([1-8])$/i.exec(String(value))?.[1]?.padStart(2, "0");

function dateRanges(params) {
  const today = new Date(`${new Date().toLocaleDateString("sv-SE", {timeZone: timezone})}T00:00:00Z`);
  const daysAgo = days => dateKey(new Date(today.getTime() - days * 86_400_000));
  return {
    today: [daysAgo(0), daysAgo(0)],
    yesterday: [daysAgo(1), daysAgo(1)],
    week: [daysAgo((today.getUTCDay() + 6) % 7), daysAgo(0)],
    month: [daysAgo(0).slice(0, 8) + "01", daysAgo(0)],
    ...(params?.start && params?.end && {custom: [params.start, params.end]}),
  };
}

async function remoteArchive(base, {bay, day, grouped}, signal) {
  const {data: calendar} = await axios.get(`${base}/api/engagement/calendar`, {
    params: {timezone, days: 15, bay_id: bay, start_date: day, end_date: day}, signal,
  });
  const {data} = await axios.get(`${base}/api/engagement/events`, {
    params: {timezone, bay_id: bay, start_date: day ?? calendar.date_range?.start_date,
      end_date: day ?? calendar.date_range?.end_date, limit: 500}, signal,
  });
  if (!Array.isArray(calendar.days) || !Array.isArray(data.events)) throw new Error("Unsupported event archive response.");
  const events = data.events.filter(event => typeof event.camera_id === "string" && Number.isFinite(event.timestamp))
    .map((event, index) => ({
      id: `${event.camera_id}:${event.timestamp}:${event.event_type}:${index}`,
      camera: event.camera_id,
      occurred_at: event.timestamp > 1e11 ? event.timestamp / 1000 : event.timestamp,
      status: "unavailable",
      type: typeof event.event_type === "string" ? event.event_type.replaceAll("_", " ") : undefined,
    })).sort((a, b) => b.occurred_at - a.occurred_at);
  const daysFor = id => {
    const entry = calendar.by_bay?.find(row => bayKey(row.bay_id) === bayKey(id));
    const counts = new Map((entry?.days ?? (bay ? calendar.days : []))
      .map(row => [row.date, row.total_engagements ?? row.engagements ?? row.count]));
    return calendar.days.map(row => ({date: row.date, count: counts.get(row.date) ?? 0})).sort((a, b) => b.date.localeCompare(a.date));
  };
  if (!grouped) return {days: daysFor(bay), events, next_before: null};
  return {bays: Object.fromEntries(bays.map(({id}) => [id, {
    days: daysFor(id), events: events.filter(event => event.camera.startsWith(id + "-")).slice(0, 12), next_before: null,
  }]))};
}

export async function getPerformanceSnapshot(url, signal) {
  const path = remoteBackend ? "/api/performance" : "/system/performance";
  const response = await axios.get(`${normalizeBase(url)}${path}`, {timeout: TIMEOUT_MS, signal});
  const raw = response.data;
  if (!remoteBackend || raw.metrics) return raw;
  const timestamp = Date.parse(raw.timestamp);
  if (!Number.isFinite(timestamp) || !raw.cpu) throw new Error("Unsupported performance response.");
  const accelerators = raw.accelerators;
  return {timestamp, metrics: {
    cpu: {value: raw.cpu.utilization_percent, source: "Remote CPU"},
    gpu: {value: raw.accelerators?.gpu_utilization_percent},
    temperature: {value: raw.temperature?.celsius, source: raw.temperature?.sensor},
    npu: {value: accelerators?.npu_utilization_percent, source: "Remote NPU",
      engines: accelerators?.npu_devices?.map(device => ({index: device.device_id, utilization_percent: device.utilization_percent}))},
  }};
}

export async function getEngagementSummary(url, params, signal, selected = "today") {
  const path = remoteBackend ? "/api/engagement/summary" : "/system/engagements";
  const ranges = remoteBackend ? dateRanges(params) : {};
  const [start, end] = ranges[selected] ?? ranges.today ?? [];
  const query = remoteBackend ? {timezone, start_date: start, end_date: end} : params;
  const requests = [axios.get(`${normalizeBase(url)}${path}`, {params: query, timeout: TIMEOUT_MS, signal})];
  if (remoteBackend) {
    requests.push(axios.get(`${normalizeBase(url)}/api/status`, {timeout: TIMEOUT_MS, signal}).catch(() => null));
  }
  const [response, statusResponse] = await Promise.all(requests);
  const status = statusResponse?.data?.status ?? statusResponse?.data ?? {};
  const summary = response.data ?? {};
  const statusDetails = {
    total_not_engaged: status?.total_not_engaged ?? summary.total_not_engaged,
    not_engaged_by_bay: status?.not_engaged_by_bay ?? summary.not_engaged_by_bay,
    not_engaged_period_totals: status?.not_engaged_period_totals ?? summary.not_engaged_period_totals,
    hook_location: status?.hook_location ?? status?.hook_position ?? status?.hook_lug_position ?? status?.hook_lug_state ?? status?.hook_state ?? status?.state_key,
    status_timestamp: status?.timestamp ?? status?.updated_at ?? status?.last_updated ?? status?.generated_at
      ?? status?.last_timestamp ?? status?.timestamp_details?.timestamp ?? status?.ts ?? summary.timestamp,
  };
  if (!remoteBackend || response.data?.periods) return {...response.data, ...statusDetails};
  const raw = response.data;
  const totals = raw.period_totals ?? {};
  const rows = raw.by_bay;
  if (!Array.isArray(rows)) throw new Error("Unsupported charging analysis response.");
  const names = {today: "today", yesterday: "yesterday", week: "this_week", month: "this_month"};
  const lifts = value => value?.lift_total ?? value?.total_lifts ?? value?.detected_lifts ?? value?.lifts;
  const misses = value => value?.missed_total ?? value?.total_missed ?? value?.missed_engagements ?? value?.missed;
  const byBay = metric => Object.fromEntries(rows.flatMap(row => {
    const bay = bayKey(row.bay_id);
    const count = metric(row);
    return bay && Number.isFinite(count) ? [[`Bay-${bay}`, count]] : [];
  }));
  return {
    ...statusDetails,
    lift_since: raw.lift_since,
    missed_since: raw.missed_since,
    periods: Object.fromEntries(Object.entries(ranges).map(([key, [start, end]]) => {
      const entry = totals[names[key]] ?? totals[key];
      const active = key === selected;
      return [key, {
        start, end,
        lift_total: active ? lifts(raw) ?? lifts(entry) : lifts(entry),
        missed_total: active ? misses(raw) ?? misses(entry) : misses(entry),
        ...(active && {lift_bays: byBay(lifts), missed_bays: byBay(misses)}),
      }];
    })),
  };
}

export async function getEngagementEvents(url, {before, bay, day, grouped}, signal) {
  if (remoteBackend) return remoteArchive(normalizeBase(url), {bay, day, grouped}, signal);
  const response = await axios.get(`${normalizeBase(url)}/system/engagement-events`, {
    params: {before, bay, day, grouped: grouped ? 1 : undefined}, timeout: TIMEOUT_MS, signal,
  });
  return response.data;
}

export async function getNetworkCameraAddresses(url, signal) {
  if (remoteBackend) {
    const {data} = await axios.post(`${normalizeBase(url)}/api/poe/scan`, undefined, {
      headers: csrfHeaders(), timeout: 20_000, signal,
    });
    const devices = data.addresses ?? data.devices ?? data.cameras ?? data;
    if (!Array.isArray(devices)) throw new Error("Unsupported camera scan response.");
    return [...new Set(devices.map(device => typeof device === "string" ? device : device?.ip_address ?? device?.ip)
      .filter(address => typeof address === "string" && address.trim()))];
  }
  const response = await axios.get(`${normalizeBase(url)}/camera-admin/network-devices`, {timeout: 7000, signal});
  return response.data.addresses;
}

export async function getPoeAssignments(url, signal) {
  if (remoteBackend) {
    const {data} = await axios.get(`${normalizeBase(url)}/api/poe/assignments`, {timeout: TIMEOUT_MS, signal});
    const rows = data.assignments ?? data;
    if (!rows || typeof rows !== "object") throw new Error("Unsupported PoE assignment response.");
    return Object.fromEntries(Object.entries(rows).flatMap(([key, row]) => {
      const bay = bayKey(row?.bay_id);
      const side = {left: 1, right: 2}[String(row?.side).toLowerCase()];
      const id = bay && side ? `Bay-${bay}-Camera-${side}` : key;
      return /^Bay-0[1-8]-Camera-[12]$/.test(id) && typeof row?.ip_address === "string"
        ? [[id, normalizePoeAssignment(row)]] : [];
    }));
  }
  const response = await axios.get(`${normalizeBase(url)}/camera-admin/poe-assignments`, {timeout: TIMEOUT_MS, signal});
  return response.data.assignments;
}

const poeAssignmentEndpoint = (url, cameraId) => `${normalizeBase(url)}/camera-admin/poe-assignments/${encodeURIComponent(cameraId)}`;
function normalizePoeAssignment(row) {
  const port = row.poe_switch_port ?? row.switch_port ?? "";
  return {ip_address: row.ip_address, switch_port: /^\d+$/.test(String(port)) ? Number(port) : port};
}

function poeCameraSlot(cameraId) {
  const match = /^(Bay-0[1-8])-Camera-([12])$/.exec(cameraId);
  if (!match) throw new Error("Invalid bay camera.");
  return {bay_id: match[1], side: match[2] === "1" ? "left" : "right"};
}

export async function requestPoeAssignment(url, cameraId, {assignment, signal} = {}) {
  if (remoteBackend) {
    if (!assignment) return (await getPoeAssignments(url, signal))[cameraId];
    if (assignment.username || assignment.password) throw new Error("The remote API does not support camera credentials.");
    const payload = {...poeCameraSlot(cameraId),
      ip_address: assignment.ip_address, poe_switch_port: String(assignment.switch_port)};
    const {data} = await axios.post(`${normalizeBase(url)}/api/poe/assignments`, payload, {
      headers: csrfHeaders(), timeout: TIMEOUT_MS, signal,
    });
    return normalizePoeAssignment(data?.assignment ?? (data?.ip_address ? data : payload));
  }
  const response = await axios.request({
    url: poeAssignmentEndpoint(url, cameraId),
    method: assignment ? "POST" : "GET",
    data: assignment,
    timeout: TIMEOUT_MS,
    signal,
  });
  return response.data.assignment;
}

export async function removePoeAssignment(url, cameraId, assignment) {
  if (remoteBackend) {
    const {bay_id, side} = poeCameraSlot(cameraId);
    await axios.delete(`${normalizeBase(url)}/api/poe/assignments/${encodeURIComponent(bay_id)}/${encodeURIComponent(side)}`, {
      headers: csrfHeaders(), timeout: TIMEOUT_MS,
    });
    return;
  }
  await axios.delete(poeAssignmentEndpoint(url, cameraId), {data: assignment, timeout: TIMEOUT_MS});
}

/** Hook Detector status in the shape consumed by the dashboard components. */
export async function getState(url, signal, statusPath = "/status") {
  const r = await axios.get(`${normalizeBase(url)}${statusPath}`, {timeout: TIMEOUT_MS, signal});
  const status = r.data;
  const cameraConnected = Boolean(status.camera_connected ?? status.frame_ready);
  return {
    ...status,
    api_url: normalizeBase(url),
    state: status.alert_state || status.state,
    state_title: cameraConnected
      ? status.status || status.state
      : status.camera_error || "Camera unavailable",
    severity: status.severity,
    camera_connected: cameraConnected,
    camera_error: status.camera_error || (status.frame_ready ? "" : status.status),
    hook_found: status.hook === "Detected",
    inserted: status.inserted === "Yes",
    inserted_text: status.inserted,
    lift_enabled: status.lift === "Normal",
    voice_alert: status.alert_main,
    ts: Date.now() / 1000,
  };
}

/** Liveness and camera status of the Hook Detector service. */
export async function getHealth(url) {
  const r = await axios.get(`${normalizeBase(url)}/health`, {timeout: TIMEOUT_MS});
  return r.data;
}
