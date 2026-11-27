import {apiErrorMessage, remoteBackend} from "../api/axios";
import {useEffect, useState} from "react";
import {NavLink, Outlet} from "react-router-dom";
import {CalendarDays, ChartNoAxesColumnIncreasing, ChevronRight} from "lucide-react";
import Header from "../components/Header";
import {useAuth} from "../auth/AuthContext";
import {getVisibleBays, getVisiblePeriods, HOOK_DETECTION_API} from "../config/cameras";
import {getEngagementSummary} from "../services/api";
import "./EngagementDashboard.css";

const PERIODS = [
  ["today", "Today"], ["yesterday", "Yesterday"], ["week", "This week"], ["month", "This month"],
];
const countFormat = new Intl.NumberFormat();
const dayFormat = new Intl.DateTimeFormat(undefined, {dateStyle: "medium", timeZone: "UTC"});
const formatDay = (day) => dayFormat.format(new Date(`${day}T00:00:00Z`));
const numericValue = value => {
  const numeric = typeof value === "number" ? value : Number(value);
  if (Number.isFinite(numeric)) return numeric;
  if (!value || typeof value !== "object") return undefined;
  return numericValue(value.total_not_engaged ?? value.not_engaged ?? value.count ?? value.total ?? value.value);
};
const formatStatusTime = value => {
  if (value === undefined || value === null) return "Timestamp unavailable";
  const numeric = Number(value);
  const date = new Date(Number.isFinite(numeric) ? (numeric < 1e12 ? numeric * 1000 : numeric) : value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
};
const formatRange = ({start, end}) => start === end ? formatDay(start) : `${formatDay(start)} – ${formatDay(end)}`;

export function EngagementLayout() {
  return (
    <>
      <Header />
      <main className="engagement-page">
        <nav className="engagement-section-nav" aria-label="Charging Analysis sections">
          <NavLink to="/engagements" end>Overview</NavLink>
          <NavLink to="/engagements/events">Hook Engagement Event Logs</NavLink>
        </nav>
        <Outlet />
      </main>
    </>
  );
}

export default function EngagementDashboard() {
  const {user} = useAuth();
  const bays = getVisibleBays(user.license_id);
  const [data, setData] = useState(null);
  const [selected, setSelected] = useState("today");
  const [custom, setCustom] = useState(null);
  const [dates, setDates] = useState({start: "", end: ""});
  const [error, setError] = useState("");
  const [dateError, setDateError] = useState("");
  const remotePeriod = remoteBackend ? selected : undefined;

  useEffect(() => {
    const controller = new AbortController();
    let timer;
    let pending = false;
    async function update() {
      if (pending || document.hidden) return;
      pending = true;
      try {
        const summary = await getEngagementSummary(HOOK_DETECTION_API, custom, controller.signal, remotePeriod);
        if (!controller.signal.aborted) {
          setData(summary);
          setError("");
        }
      } catch (failure) {
        if (!controller.signal.aborted) setError(apiErrorMessage(failure, "Could not load engagement totals."));
      } finally {
        pending = false;
        if (!controller.signal.aborted && !document.hidden) timer = setTimeout(update, 5000);
      }
    }
    function visibilityChanged() {
      clearTimeout(timer);
      if (!document.hidden) update();
    }
    update();
    document.addEventListener("visibilitychange", visibilityChanged);
    return () => {
      controller.abort();
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visibilityChanged);
    };
  }, [custom, remotePeriod]);

  function applyRange(event) {
    event.preventDefault();
    if (dates.end < dates.start) {
      setDateError("The end date must be on or after the start date.");
      return;
    }
    setDateError("");
    setCustom(dates);
    setSelected("custom");
  }

  const periods = getVisiblePeriods(data?.periods, bays);
  const customIsCurrent = periods?.custom?.start === custom?.start && periods?.custom?.end === custom?.end;
  const current = selected === "custom" && !customIsCurrent ? undefined : periods?.[selected];
  const label = selected === "custom" ? "Custom range" : PERIODS.find(([key]) => key === selected)[1];
  const missed = current && (remoteBackend || (data?.missed_since && current.end >= data.missed_since)) ? current.missed_total : undefined;
  const liftTotal = period => remoteBackend || (data?.lift_since && period?.end >= data.lift_since) ? period?.lift_total : undefined;
  const lifted = liftTotal(current);
  const outcomes = (lifted ?? 0) + (missed ?? 0);
  const successShare = outcomes && missed !== undefined && lifted !== undefined ? Math.round(lifted / outcomes * 100) : null;
  const ceiling = Math.max(1, ...bays.map(({id}) => Math.max(current?.lift_bays?.[id] ?? 0, current?.missed_bays?.[id] ?? 0)));
  const notEngagedPeriod = data?.not_engaged_period_totals?.[selected];
  const totalNotEngaged = numericValue(notEngagedPeriod) ?? numericValue(data?.total_not_engaged);
  const statusTimestamp = data?.status_timestamp ?? data?.timestamp ?? data?.updated_at;
  const notEngagedReasonValue = key => numericValue(notEngagedPeriod?.[key])
    ?? numericValue(data?.not_engaged_period_totals?.[key]?.[selected])
    ?? numericValue(data?.not_engaged_by_reason?.[key]);
  const notEngagedForBay = id => {
    const values = data?.not_engaged_by_bay;
    if (Array.isArray(values)) {
      const row = values.find(entry => entry?.bay_id === id || entry?.bay === id);
      return numericValue(row);
    }
    return numericValue(values?.[id]);
  };

  return (
    <>
        <header className="workspace-heading">
          <div>
            <h1><ChartNoAxesColumnIncreasing size={30} aria-hidden="true" /> Charging Analysis</h1>
            <p>Detected coke wagon lifts and missed engagement attempts by bay</p>
          </div>
          <span className="workspace-heading-status engagement-live" data-state={error ? "offline" : data ? "live" : "loading"}>
            {error ? "Connection interrupted" : data ? "Live totals" : "Connecting…"}
          </span>
        </header>

        <section className="engagement-periods" aria-label="Lift totals by period">
          {PERIODS.map(([key, title]) => {
            const period = periods?.[key];
            return (
              <button key={key} type="button" className="engagement-period" data-period={key}
                aria-pressed={key === selected} onClick={() => setSelected(key)}>
                <span><CalendarDays size={17} aria-hidden="true" /> {title}<ChevronRight size={16} aria-hidden="true" /></span>
                <strong>{liftTotal(period) !== undefined ? countFormat.format(liftTotal(period)) : "—"}</strong>
                <small>{period ? formatRange(period) : "Waiting for totals"}</small>
              </button>
            );
          })}
        </section>

        <section className="engagement-report" aria-labelledby="engagement-report-title">
          <div className="engagement-report-heading">
            <div>
              <h2 id="engagement-report-title">Engagements by bay</h2>
              <p>{label}{current ? ` · ${formatRange(current)}` : ""}</p>
            </div>

          </div>

          <form className="engagement-range" onSubmit={applyRange} aria-label="Custom date range">
            <label>Start date<input type="date" required value={dates.start}
              onChange={(event) => { setDates({...dates, start: event.target.value}); setDateError(""); }} /></label>
            <label>End date<input type="date" required min={dates.start || undefined} value={dates.end}
              onChange={(event) => { setDates({...dates, end: event.target.value}); setDateError(""); }} /></label>
            <button type="submit">Apply date range</button>
          </form>
          {dateError && <p className="engagement-error" role="alert">{dateError}</p>}
          {error && <p className="engagement-error" role="status">{error}</p>}
          <div className="engagement-charts">
            <section className="engagement-comparison" aria-labelledby="engagement-comparison-title">
              <h3 id="engagement-comparison-title">Bay comparison</h3>
              <div className="engagement-legend">
                <span>Detected lifts</span><span data-outcome="missed">Missed</span>
              </div>
              <ul className="engagement-outcome-bars" key={`${selected}-${current?.start}-${current?.end}`}>
                {bays.map(({id}) => {
                  const lifts = lifted === undefined ? undefined : current.lift_bays?.[id];
                  const missedCount = missed === undefined ? undefined : current.missed_bays[id];
                  return (
                    <li key={id}>
                      <strong>{id.replace("-", " ")}</strong>
                      <div className="engagement-bar-pair">
                        {[["Lifts", lifts], ["Missed", missedCount]].map(([name, count]) => (
                          <div className="engagement-outcome-bar" data-outcome={name.toLowerCase()} key={name}>
                            <div className="engagement-bar" aria-hidden="true">
                              <div style={{"--fill": `${(count ?? 0) / ceiling * 100}%`}} />
                            </div>
                            <span><span className="sr-only">{name}: </span>{count === undefined ? "—" : countFormat.format(count)}</span>
                          </div>
                        ))}
                      </div>
                    </li>
                  );
                })}
              </ul>
            </section>
            <section className="engagement-outcomes" aria-labelledby="engagement-outcomes-title">
              <h3 id="engagement-outcomes-title">Outcome breakdown</h3>
              <div className="engagement-donut" style={{"--confirmed-share": `${successShare ?? 0}%`}}
                data-available={successShare !== null}>
                <div><strong>{successShare === null ? "—" : `${successShare}%`}</strong><span>Of recorded outcomes</span></div>
              </div>
              <dl>
                <div><dt>Detected lifts</dt><dd>{lifted !== undefined ? countFormat.format(lifted) : "—"}</dd></div>
                <div data-outcome="missed"><dt>Missed</dt><dd>{missed === undefined ? "—" : countFormat.format(missed)}</dd></div>
              </dl>
            </section>
          </div>
          <section className="engagement-status-panel" aria-labelledby="not-engaged-title">
            <div>
              <h3 id="not-engaged-title">Not-engaged status</h3>
              <p>
                Hook position: <strong>{data?.hook_location || "Unavailable"}</strong>
                <span> · Updated {formatStatusTime(statusTimestamp)}</span>
              </p>
            </div>
            <div className="engagement-status-total">
              <span>Selected period</span>
              <strong>{totalNotEngaged === undefined ? "—" : countFormat.format(totalNotEngaged)}</strong>
            </div>
            <ul className="engagement-status-bays">
              {bays.map(({id}) => {
                const count = notEngagedForBay(id);
                return <li key={id}><span>{id.replace("-", " ")}</span><strong>{count === undefined ? "—" : countFormat.format(count)}</strong></li>;
              })}
            </ul>
            <div className="engagement-status-reasons">
              {[["HOOK_INSIDE_LUG", "Inside lug"], ["HOOK_OUTSIDE_LUG", "Outside lug"]].map(([key, label]) => (
                <span key={key}><span>{label}</span><strong>{notEngagedReasonValue(key) === undefined ? "—" : countFormat.format(notEngagedReasonValue(key))}</strong></span>
              ))}
            </div>
          </section>
          <p className="engagement-data-note">
            {data?.lift_since ? `Lift tracking began ${formatDay(data.lift_since)}. Earlier lifts are unavailable. ` :
              lifted === undefined && "Lift tracking data is unavailable. "}
            {data?.missed_since ? `Missed tracking began ${formatDay(data.missed_since)}. Earlier missed attempts are unavailable.` :
              missed === undefined && "Missed engagement data is unavailable."}
          </p>
        </section>
    </>
  );
}
