import {getVisibleBays, getVisiblePeriods, HOOK_DETECTION_API as api} from "../config/cameras";
import {remoteBackend} from "../api/axios";
import {getEngagementEvents, getEngagementSummary, getPerformanceSnapshot, getPoeAssignments, getState} from "../services/api";

const routes = {
  bays: ["/", "Bays"],
  settings: ["/settings", "Camera Settings"],
  performance: ["/performance", "System Performance"],
  analysis: ["/engagements", "Charging Analysis"],
  events: ["/engagements/events", "Hook Engagement Event Logs"],
};
const label = (id) => id.replace("Bay-", "Bay ");
const count = (n, noun) => `${n} ${noun}${n === 1 ? "" : "s"}`;
const reading = (value, unit) => Number.isFinite(value) ? `${Number(value.toFixed(1))} ${unit}` : "unavailable";

export async function answerQuestion(question, user, signal) {
  const admin = user.role === "admin";
  const bays = getVisibleBays(user.license_id);
  const query = question.toLowerCase();
  const bayNumber = query.match(/\bbay[\s#-]*(\d+)/)?.[1];
  const bay = bays.find(({id}) => id === `Bay-${bayNumber?.padStart(2, "0")}`);
  if (bayNumber && !bay) return {text: `Choose ${label(bays[0].id)} through ${label(bays.at(-1).id)}.`};
  const settings = /\b(poe|ip|assignments?|mappings?|settings|maintenance|switch port)\b/.test(query);
  const performance = /\b(cpu|gpu|npu|deepx|temperature|performance|voltage|clock)\b/.test(query);
  const events = /\b(logs?|clips?|recordings?|archive|footage|videos?|replay)\b/.test(query);
  const analysis = /\b(charging|engagements?|lifts?|lifted|locks?|locking|counts?|totals?|busiest|today|yesterday|week|month)\b/.test(query);
  const cameras = /\b(cameras?|online|offline|webcam|connected|status)\b/.test(query);
  const section = settings ? "settings" : performance ? "performance" : events ? "events" : analysis ? "analysis" : "bays";
  const cameraRoute = bay ? [`/bay/${bay.id}`, label(bay.id)] : routes.bays;
  const route = section === "bays" ? cameraRoute : routes[section];

  if (!admin && section !== "bays") return {text: `${route[1]} requires administrator access.`, route: cameraRoute};
  if (/^(?:please )?(open|go to|navigate to|take me to)\b/.test(query)) return {text: `Open ${route[1]} below.`, route};

  if (settings) {
    const assignments = await getPoeAssignments(api, signal);
    const cameras = (bay ? [bay] : bays).flatMap(({hooks}) => hooks);
    const assigned = cameras.filter(({id}) => assignments?.[id]);
    const lines = bay ? assigned.map(({id, name}) => `${name}: ${assignments[id].ip_address}, PoE port ${assignments[id].switch_port}`) : [];
    return {text: [`${assigned.length} of ${cameras.length} cameras have saved IP and PoE port mappings.`, ...lines].join("\n"), route};
  }

  if (performance) {
    const {metrics} = await getPerformanceSnapshot(api, signal);
    const lines = [
      `CPU: ${reading(metrics?.cpu?.value, "%")}`,
      `GPU: ${reading(metrics?.gpu?.value, "%")}`,
      `System temperature: ${reading(metrics?.temperature?.value, "\u00b0C")}`,
      ...[0, 1, 2].map(index => {
        const core = metrics?.npu?.engines?.find(engine => engine.index === index);
        return `NPU ${index}: ${core ? [reading(core.temperature_c, "\u00b0C"), reading(core.clock_mhz, "MHz"), reading(core.voltage_mv, "mV")].join(", ") : "unavailable"}`;
      }),
    ];
    return {text: lines.join("\n"), route};
  }

  if (events) {
    const {bays: archive} = await getEngagementEvents(api, {grouped: true}, signal);
    const entries = (bay ? [bay] : bays).map(({id}) => [id, archive?.[id]]);
    const lines = entries.filter(([, data]) => Array.isArray(data?.days)).map(([id, data]) => [
      id, data.days.reduce((sum, day) => sum + day.count, 0),
    ]);
    if (!lines.length) return {text: "Recording history is unavailable.", route};
    const total = lines.reduce((sum, [, value]) => sum + value, 0);
    return {
      text: [`${count(total, "logged event")} in the 15-day archive${bay ? ` for ${label(bay.id)}` : ""}.`,
        ...lines.filter(([, value]) => value > 0 && !bay).map(([id, value]) => `${label(id)}: ${value}`),
        "Open the event logs to browse dates and play available clips."].join("\n"),
      route,
    };
  }

  if (analysis) {
    const dates = query.match(/\b\d{4}-\d{2}-\d{2}\b/g) ?? [];
    const range = dates.length ? {start: dates[0], end: dates.at(-1)} : undefined;
    const periodKey = range ? "custom" : /yesterday/.test(query) ? "yesterday" : /month/.test(query) ? "month" : /week/.test(query) ? "week" : "today";
    if (range?.start > range?.end) return {text: "Enter the start date before the end date.", route};
    const summary = await getEngagementSummary(api, range, signal, periodKey);
    const {lift_since} = summary;
    const periods = getVisiblePeriods(summary.periods, bays);
    const totalFor = period => period && (remoteBackend || period.end >= lift_since)
      ? (bay ? period.lift_bays?.[bay.id] : period.lift_total) : undefined;
    if (!range && /\btoday\b/.test(query) && /\byesterday\b/.test(query) && totalFor(periods?.today) !== undefined && totalFor(periods?.yesterday) !== undefined) {
      const today = totalFor(periods.today);
      const yesterday = totalFor(periods.yesterday);
      const difference = today - yesterday;
      const comparison = difference ? `${Math.abs(difference)} ${difference > 0 ? "more" : "fewer"} so far today.` : "The totals are equal.";
      return {text: `${bay ? label(bay.id) : "All bays"}: ${today} today, ${yesterday} yesterday. ${comparison}`, route};
    }
    const period = periods?.[periodKey];
    if (!period) return {text: "Engagement totals are unavailable for that period.", route};
    const value = totalFor(period);
    if (value === undefined) return {text: "Lift tracking is unavailable for that period.", route};
    const ranked = Object.entries(period.lift_bays ?? {}).sort((a, b) => b[1] - a[1]);
    const peak = ranked[0]?.[1];
    const leaders = ranked.filter(([, total]) => total === peak).map(([id]) => label(id));
    const insight = !bay && peak > 0 ? `\nMost detected lifts: ${leaders.join(", ")} (${peak} each).` : "";
    return {text: `${bay ? label(bay.id) : "All bays"}: ${count(value, "detected lift")} (${period.start}${period.end === period.start ? "" : ` to ${period.end}`}).${insight}`, route};
  }

  if (cameras) {
    const hooks = (bay ? [bay] : bays).flatMap(({hooks}) => hooks);
    const results = await Promise.allSettled(hooks.map(({endpoint}) => getState(endpoint, signal)));
    const statuses = results.map(result => result.status === "fulfilled" ? result.value.camera_connected ? "online" : "offline" : "unavailable");
    const online = statuses.filter(status => status === "online").length;
    const unavailable = statuses.filter(status => status === "unavailable").length;
    const lines = bay ? hooks.map((hook, i) => `${hook.name}: ${statuses[i]}`) :
      [`${online} online, ${hooks.length - online - unavailable} offline, ${unavailable} unavailable across ${bays.length} bays.`];
    return {text: lines.join("\n"), route};
  }

  if (bay || /\bbays\b/.test(query)) return {text: `Open ${route[1]} below.`, route};
  return {text: admin
    ? "Ask about camera status, IP and PoE mappings, engagement totals, event recordings, or live CPU, GPU, NPU and temperature readings. Try including a bay number or a date range."
    : "Ask about live camera status or which bay to open. For example, ask whether the cameras in your area are online."};
}

