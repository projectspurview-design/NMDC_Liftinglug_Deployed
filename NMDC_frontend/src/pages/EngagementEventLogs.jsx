import {apiErrorMessage, remoteBackend} from "../api/axios";
import {useEffect, useRef, useState} from "react";
import {CalendarDays, Film, ScanLine, Video} from "lucide-react";
import {useAuth} from "../auth/AuthContext";
import {getVisibleBays, HOOK_DETECTION_API} from "../config/cameras";
import {getEngagementEvents} from "../services/api";
import "./EngagementEventLogs.css";

const displayTime = new Intl.DateTimeFormat(undefined, {dateStyle: "medium", timeStyle: "medium"});
const displayDay = new Intl.DateTimeFormat(undefined, {month: "short", day: "numeric", timeZone: "UTC"});
const formatDay = (day) => displayDay.format(new Date(`${day}T00:00:00Z`));
const cameraName = (id) => `${id.replace(/^Bay-(\d+)-Camera-\d+$/, "Bay $1")} · ${id.endsWith("-1") ? "Left camera" : "Right camera"}`;
const statusLabels = {recording: "Recording", ready: "Ready", partial: "Partial video", unavailable: "Unavailable"};

function ClipPlayer({event}) {
  const video = useRef(null);
  const [playbackError, setPlaybackError] = useState(false);
  const playable = event?.status === "ready" || event?.status === "partial";

  return (
    <section className="engagement-clip" aria-label="Engagement video">
      <header className="engagement-clip-header">
        <div>
          <h2>{event ? cameraName(event.camera) : "Engagement video"}</h2>
          {event && <time dateTime={new Date(event.occurred_at * 1000).toISOString()}>{displayTime.format(event.occurred_at * 1000)}</time>}
        </div>
        {event && <span className="engagement-event-state" data-status={event.status}>{statusLabels[event.status]}</span>}
      </header>
      {playable ? (
        <>
          <video ref={video} controls playsInline preload="metadata" aria-label={`Engagement at ${cameraName(event.camera)}`}
            src={`${HOOK_DETECTION_API}/system/engagement-events/${event.id}/clip`} onError={() => setPlaybackError(true)} />
          {playbackError && <p role="alert">The video could not be played.</p>}
          <div className="engagement-clip-timeline" aria-label="Recording window"
            style={{"--lock-point": `${event.before_seconds / (event.before_seconds + event.after_seconds || 1) * 100}%`}}>
            <span>{event.before_seconds}s before</span>
            <span className="engagement-lock-marker">Lock</span>
            <span>{event.after_seconds}s after</span>
          </div>
          <div className="engagement-clip-actions">
            <button type="button" onClick={() => { if (video.current?.readyState) video.current.currentTime = event.before_seconds; }}>
              <ScanLine size={16} aria-hidden="true" /> Jump to lock
            </button>
          </div>
          {event.status === "partial" && <p>Some camera frames were unavailable during this recording.</p>}
        </>
      ) : (
        <div className="engagement-clip-empty" data-status={event?.status}>
          <div className="engagement-clip-icon"><Video size={22} aria-hidden="true" /></div>
          <p>{!event ? "Select an event to view its clip." : event.status === "recording" ?
            "Recording the post-engagement window. This clip will appear here when it is ready." :
            "A video is unavailable for this event."}</p>
        </div>
      )}
    </section>
  );
}

function BayEventList({bayId, latest, activeBay, selectedId, onSelect}) {
  const [page, setPage] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const activePage = page?.day && !latest?.days?.some(({date}) => date === page.day) ? null : page;
  const data = activePage ?? latest;

  async function loadPage(day, before) {
    setError("");
    setLoading(true);
    try {
      const result = await getEngagementEvents(HOOK_DETECTION_API, {bay: bayId, day, before});
      setPage({...result, day});
    } catch (failure) {
      setError(apiErrorMessage(failure, "Could not load the recordings for this bay."));
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="engagement-event-list" data-active={activeBay} aria-labelledby={`${bayId}-event-title`}>
      <header className="engagement-bay-heading">
        <h2 id={`${bayId}-event-title`}>{bayId.replace("-", " ")}</h2>
        <CalendarDays size={16} aria-hidden="true" />
      </header>
      {latest?.days?.length > 0 && (
        <div className="engagement-recording-days" role="group" aria-label="Recording dates">
          {latest.days.map(({date, count}, index) => (
            <button key={date} type="button" aria-pressed={date === activePage?.day} disabled={loading}
              data-events={count > 0} data-today={index === 0}
              aria-label={`${formatDay(date)}, ${count} ${count === 1 ? "event" : "events"}`}
              onClick={() => loadPage(date)}>
              <time dateTime={date}>{formatDay(date)}</time>
              <span>{count}</span>
            </button>
          ))}
        </div>
      )}
      {activePage?.day && <p className="engagement-recording-day-label">{remoteBackend ? "Events" : "Recordings"} for {formatDay(activePage.day)}</p>}
      {data?.events.length === 0 && <p className="engagement-bay-empty">{activePage?.day ? "No events recorded on this date." : "No events recorded for this bay."}</p>}
      {data?.events.length > 0 && <ul>
        {data.events.map((event) => (
          <li key={event.id}>
            <button type="button" aria-pressed={event.id === selectedId} onClick={() => onSelect(event)}>
              <span>
                <strong>{event.camera.endsWith("-1") ? "Left camera" : "Right camera"}{event.type && ` · ${event.type}`}</strong>
                <time dateTime={new Date(event.occurred_at * 1000).toISOString()}>{displayTime.format(event.occurred_at * 1000)}</time>
              </span>
              <span className="engagement-event-state" data-status={event.status}>{statusLabels[event.status]}</span>
            </button>
          </li>
        ))}
      </ul>}
      {error && <p role="status">{error}</p>}
      {(activePage || data?.next_before) && (
        <div className="engagement-event-paging">
          {activePage && <button type="button" disabled={loading} onClick={() => { setPage(null); setError(""); }}>Latest events</button>}
          {data.next_before && <button type="button" disabled={loading}
            onClick={() => loadPage(activePage?.day, data.next_before)}>Older events</button>}
        </div>
      )}
    </section>
  );
}

export default function EngagementEventLogs() {
  const {user} = useAuth();
  const bays = getVisibleBays(user.license_id);
  const [selected, setSelected] = useState(null);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const player = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    let timer;
    let pending = false;
    async function load() {
      if (document.hidden || pending) return;
      pending = true;
      try {
        const next = await getEngagementEvents(HOOK_DETECTION_API, {grouped: true}, controller.signal);
        if (!controller.signal.aborted) {
          setData(next.bays);
          setError("");
        }
      } catch (failure) {
        if (!controller.signal.aborted) setError(apiErrorMessage(failure, "Could not load engagement event logs."));
      } finally {
        pending = false;
        if (!controller.signal.aborted && !document.hidden) timer = setTimeout(load, 2000);
      }
    }
    function showPage() {
      clearTimeout(timer);
      if (!document.hidden) load();
    }
    load();
    document.addEventListener("visibilitychange", showPage);
    return () => {
      controller.abort();
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", showPage);
    };
  }, []);

  function selectEvent(event) {
    setSelected(event);
    if (window.matchMedia("(max-width: 900px)").matches) player.current?.scrollIntoView({block: "nearest"});
  }

  const selectedBay = selected?.camera.split("-Camera-")[0];
  const current = data?.[selectedBay]?.events.find((event) => event.id === selected.id) ?? selected;

  return (
    <>
      <header className="workspace-heading">
        <div>
          <h1><Film size={30} aria-hidden="true" /> Hook Engagement Event Logs</h1>
          <p>Review camera footage from five seconds before and after each confirmed lock.</p>
        </div>
        <div className="workspace-heading-status"><CalendarDays size={16} aria-hidden="true" /> 15-day archive</div>
      </header>
      {error && <p className="engagement-error" role="status">{error}</p>}
      {!data && !error && <p role="status">Loading events...</p>}
      <div className="engagement-logs-layout">
        <div className="engagement-bay-lists" role="region" aria-label="Bay event logs" tabIndex={0}>
          {bays.map(({id}) => (
            <BayEventList key={id} bayId={id} latest={data?.[id]} activeBay={id === selectedBay} selectedId={current?.id} onSelect={selectEvent} />
          ))}
        </div>
        <div className="engagement-clip-position" ref={player}>
          <ClipPlayer key={current?.id ?? "empty"} event={current} />
        </div>
      </div>
    </>
  );
}
