import {apiBase} from "../api/axios";

export const HOOK_DETECTION_API = apiBase;

const BAY_COUNT = 8;
const HOOKS_PER_BAY = 2;

export const bays = Array.from({length: BAY_COUNT}, (_, bayIndex) => ({
  id: `Bay-${String(bayIndex + 1).padStart(2, "0")}`,
  hooks: Array.from({length: HOOKS_PER_BAY}, (_, hookIndex) => ({
    id: `Bay-${String(bayIndex + 1).padStart(2, "0")}-Camera-${hookIndex + 1}`,
    name: hookIndex === 0 ? "Left camera" : "Right camera",
    side: hookIndex === 0 ? "Left" : "Right",
    url: HOOK_DETECTION_API,
    endpoint: `${HOOK_DETECTION_API}/camera/Bay-${String(bayIndex + 1).padStart(2, "0")}-Camera-${hookIndex + 1}`,
  })),
}));

export const cameraAreas = [
  {licenseId: "NMDC_Admin_a1", name: "Area 1", bays: bays.slice(0, 4)},
  {licenseId: "NMDC_Admin_a2", name: "Area 2", bays: bays.slice(4)},
];

export function getCameraArea(licenseId) {
  if (typeof licenseId !== "string") return undefined;
  const normalized = licenseId.trim().toLowerCase();
  return cameraAreas.find(area => area.licenseId.toLowerCase() === normalized);
}
export const getVisibleBays = licenseId => getCameraArea(licenseId)?.bays ?? bays;

export function getVisiblePeriods(periods, visibleBays) {
  if (!periods || visibleBays === bays) return periods;
  return Object.fromEntries(Object.entries(periods).map(([key, period]) => {
    if (!period) return [key, period];
    const areaPeriod = {...period};
    for (const prefix of ["", "lift_", "missed_"]) {
      const counts = period[prefix + "bays"];
      if (!counts) {
        delete areaPeriod[prefix + "total"];
        continue;
      }
      const scoped = Object.fromEntries(visibleBays.map(({id}) => [id, counts[id] ?? 0]));
      areaPeriod[prefix + "bays"] = scoped;
      areaPeriod[prefix + "total"] = Object.values(scoped).reduce((sum, count) => sum + count, 0);
    }
    return [key, areaPeriod];
  }));
}
