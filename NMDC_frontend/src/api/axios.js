import axios from "axios";

export const remoteBackend = import.meta.env.VITE_NMDC_REMOTE === true;
export const apiBase = (remoteBackend ? window.location.origin :
  import.meta.env.VITE_HOOK_DETECTION_API || window.location.origin).replace(/\/+$/, "");

let csrfToken;
export function setCsrfToken(token) {
  csrfToken = typeof token === "string" ? token : undefined;
}
export const csrfHeaders = () => csrfToken ? {"X-CSRF-Token": csrfToken} : {};

export const apiClient = axios.create({
  baseURL: apiBase,
  timeout: 5000,
  withCredentials: true,
});

const apiPath = path => `/api${path}`;
const pathValue = value => encodeURIComponent(String(value));

const requestData = (method, path, config = {}) => apiClient.request({
  method,
  url: apiPath(path),
  ...config,
}).then(response => response.data);

export const verifyPassword = (password, config = {}) => requestData("POST", "/auth/verify-password", {
  ...config,
  data: {password},
  headers: {...csrfHeaders(), ...config.headers},
});

// Public API endpoints exposed by the NMDC backend.
export const getHealth = config => requestData("GET", "/health", config);
export const getStatus = config => requestData("GET", "/status", config);
export const getTrainStatus = (trainId, config) =>
  requestData("GET", `/trains/${pathValue(trainId)}/status`, config);
export const getAreas = config => requestData("GET", "/areas", config);
export const getAreaStatus = (areaId, config) =>
  requestData("GET", `/areas/${pathValue(areaId)}/status`, config);
export const getPerformance = config => requestData("GET", "/performance", config);

export const getEngagementSummary = (params, config = {}) =>
  requestData("GET", "/engagement/summary", {...config, params});
export const getChargingAnalysis = (params, config = {}) =>
  requestData("GET", "/charging/analysis", {...config, params});
export const getEngagementEvents = (params, config = {}) =>
  requestData("GET", "/engagement/events", {...config, params});
export const getEngagementCalendar = (params, config = {}) =>
  requestData("GET", "/engagement/calendar", {...config, params});

// Frame endpoints return image data, so callers can choose how to render it.
export const getFrame = config => apiClient.get(apiPath("/frame"), {
  responseType: "blob",
  ...config,
});
export const getAnnotatedFrame = config => apiClient.get(apiPath("/frame/annotated"), {
  responseType: "blob",
  ...config,
});

export const shutdown = config => requestData("POST", "/shutdown", {
  ...config,
  headers: {...csrfHeaders(), ...config.headers},
});

export const getPoeAssignments = config => requestData("GET", "/poe/assignments", config);
export const savePoeAssignment = (assignment, config = {}) => requestData("POST", "/poe/assignments", {
  ...config,
  data: assignment,
  headers: {...csrfHeaders(), ...config.headers},
});
export const deletePoeAssignment = (bayId, side, config = {}) => requestData(
  "DELETE",
  `/poe/assignments/${pathValue(bayId)}/${pathValue(side)}`,
  {...config, headers: {...csrfHeaders(), ...config.headers}},
);
export const scanPoe = config => requestData("POST", "/poe/scan", {
  ...config,
  headers: {...csrfHeaders(), ...config.headers},
});

apiClient.interceptors.response.use(undefined, error => {
  if (error.response?.status === 401 && !error.config?.skipSessionExpired) {
    setCsrfToken(undefined);
    window.dispatchEvent(new Event("session-expired"));
  }
  return Promise.reject(error);
});

export function apiErrorMessage(error, fallback) {
  const data = error.response?.data;
  if (typeof data?.error === "string") return data.error;
  if (typeof data?.detail === "string") return data.detail;
  if (Array.isArray(data?.detail)) return data.detail.map(entry => entry.msg).filter(Boolean).join(". ") || fallback;
  return error.response?.status === 404 ? "This endpoint is unavailable on the configured backend." : fallback;
}
