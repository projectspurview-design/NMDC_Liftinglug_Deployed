import {useEffect, useRef, useState} from "react";
import {Activity, Cpu, Gpu, Thermometer, Timer, Workflow, Zap} from "lucide-react";
import Header from "../components/Header";
import {HOOK_DETECTION_API} from "../config/cameras";
import {getPerformanceSnapshot} from "../services/api";
import {apiErrorMessage, remoteBackend} from "../api/axios";
import "./PerformanceDashboard.css";

const METRICS = {
  cpu: {label: "CPU", name: "CPU utilization", color: "#59e4e0", unit: "%", icon: Cpu},
  gpu: {label: "GPU", name: "GPU utilization", color: "#8eafff", unit: "%", icon: Gpu},
  npu: remoteBackend
    ? {label: "NPU utilization", name: "NPU utilization", color: "#c5a2ff", unit: "%", icon: Workflow}
    : {label: "NPU temperature", name: "DEEPX peak core temperature", color: "#c5a2ff", unit: "°C", icon: Workflow},
  temperature: {label: "System temperature", name: "System temperature", color: "#ffb876", unit: "°C", icon: Thermometer},
};
const RANGES = [[30, "30 sec"], [60, "1 min"], [300, "5 min"]];
const INTERVAL_MS = 2000;
const chartTime = new Intl.DateTimeFormat(undefined, {hour: "2-digit", minute: "2-digit", second: "2-digit"});
const finite = Number.isFinite;
const format = (value, unit) => finite(value) ? `${Number(value.toFixed(1))}${unit}` : "—";

const NPU_READINGS = [
  ...(remoteBackend ? [{key: "utilization_percent", label: "Utilization", unit: "%", icon: Activity}] : []),
  {key: "voltage_mv", label: "Voltage", unit: "mV", icon: Zap},
  {key: "clock_mhz", label: "Clock", unit: "MHz", icon: Timer},
  {key: "temperature_c", label: "Temperature", unit: "°C", icon: Thermometer},
];

function NpuReadouts({npu, stale, trendSelected, onSelectTrend}) {
  return (
    <section className="npu-readouts" aria-labelledby="npu-readouts-title">
      <div className="npu-readouts-heading">
        <div>
          <h2 id="npu-readouts-title">DEEPX DX-M1 M.2</h2>
          <p>DX-M1BNM5604 · DX-RT core telemetry</p>
        </div>
        <button type="button" className="npu-trend-button" aria-pressed={trendSelected} onClick={onSelectTrend}>{METRICS.npu.label} trend</button>
      </div>
      <div className="npu-readouts-grid">
        {[0, 1, 2].map((index) => {
          const engine = npu?.engines?.find((reading) => reading.index === index);
          return (
            <article className="npu-readout" key={index}>
              <h3><Workflow size={18} aria-hidden="true" /> NPU {index}</h3>
              <dl>
                {NPU_READINGS.map(({key, label, unit, icon: Icon}) => {
                  const reported = finite(engine?.[key]);
                  return (
                    <div key={key}>
                      <dt><Icon size={15} aria-hidden="true" /> {label}</dt>
                      <dd>
                        <strong>{format(engine?.[key], ` ${unit}`)}</strong>
                        <span data-live={reported && !stale}>
                          {reported ? (stale ? "Last reading" : "Live") : "Unavailable"}
                        </span>
                      </dd>
                    </div>
                  );
                })}
              </dl>
            </article>
          );
        })}
      </div>
      {npu?.detail && <p className="npu-readouts-note" role="status">{npu.detail}</p>}
    </section>
  );
}

function HistoryChart({points, end, seconds, metric, units}) {
  const canvas = useRef(null);
  const [width, setWidth] = useState(0);
  const [cursor, setCursor] = useState(null);
  const start = end - seconds * 1000;
  const upper = units === "%" ? 100 : Math.max(100, Math.ceil(Math.max(...points.filter(({value}) => finite(value)).map(({value}) => value), 0) / 25) * 25);
  const lower = units === "%" ? 0 : Math.min(0, Math.floor(Math.min(...points.filter(({value}) => finite(value)).map(({value}) => value), 0) / 25) * 25);
  const hasData = points.some(({value}) => finite(value));
  const hovered = cursor === null ? null : points.reduce((best, point) => {
    if (!finite(point.value)) return best;
    const time = start + cursor * (end - start);
    return !best || Math.abs(point.timestamp - time) < Math.abs(best.timestamp - time) ? point : best;
  }, null);

  useEffect(() => {
    const node = canvas.current;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const node = canvas.current;
    if (!width) return;
    const height = node.clientHeight;
    const scale = window.devicePixelRatio || 1;
    node.width = Math.round(width * scale);
    node.height = Math.round(height * scale);
    const ctx = node.getContext("2d");
    ctx.scale(scale, scale);
    const left = 50;
    const top = 18;
    const bottom = height - 36;
    const right = width - 15;
    const x = (time) => left + (time - start) / (end - start) * (right - left);
    const y = (value) => bottom - (value - lower) / (upper - lower) * (bottom - top);
    ctx.font = "11px system-ui";
    ctx.fillStyle = "#a4b3cb";
    ctx.textBaseline = "middle";
    ctx.textAlign = "right";
    for (let index = 0; index <= 4; index++) {
      const value = lower + (upper - lower) * index / 4;
      const level = y(value);
      ctx.fillText(`${Math.round(value)}${units}`, left - 8, level);
      ctx.strokeStyle = "#283951";
      ctx.beginPath();
      ctx.moveTo(left, level);
      ctx.lineTo(right, level);
      ctx.stroke();
    }
    const ticks = width < 440 ? 2 : 4;
    ctx.textBaseline = "top";
    for (let index = 0; index <= ticks; index++) {
      const time = start + (end - start) * index / ticks;
      ctx.textAlign = index === 0 ? "left" : index === ticks ? "right" : "center";
      ctx.fillText(hasData ? chartTime.format(time) : index === ticks ? "Now" : `−${Math.round(seconds * (1 - index / ticks))}s`, x(time), bottom + 13);
    }
    const gradient = ctx.createLinearGradient(0, top, 0, bottom);
    gradient.addColorStop(0, `${metric.color}50`);
    gradient.addColorStop(1, `${metric.color}08`);
    const segments = [];
    let segment = [];
    for (const point of points) {
      if (!finite(point.value) || (segment.length && point.timestamp - segment[segment.length - 1].timestamp > INTERVAL_MS * 2.5)) {
        if (segment.length) segments.push(segment);
        segment = [];
      }
      if (finite(point.value)) segment.push(point);
    }
    if (segment.length) segments.push(segment);
    const trace = (entries) => {
      ctx.beginPath();
      ctx.moveTo(x(entries[0].timestamp), y(entries[0].value));
      entries.slice(1).forEach((entry) => ctx.lineTo(x(entry.timestamp), y(entry.value)));
    };
    for (const entries of segments) {
      trace(entries);
      ctx.lineTo(x(entries[entries.length - 1].timestamp), bottom);
      ctx.lineTo(x(entries[0].timestamp), bottom);
      ctx.closePath();
      ctx.fillStyle = gradient;
      ctx.fill();
      trace(entries);
      ctx.strokeStyle = metric.color;
      ctx.lineWidth = 2;
      ctx.stroke();
    }
    const dot = hovered || segments.at(-1)?.at(-1);
    if (dot) {
      if (hovered) {
        ctx.beginPath();
        ctx.strokeStyle = "#a4b3cb";
        ctx.lineWidth = 1;
        ctx.moveTo(x(dot.timestamp), top);
        ctx.lineTo(x(dot.timestamp), bottom);
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.arc(x(dot.timestamp), y(dot.value), 4, 0, Math.PI * 2);
      ctx.fillStyle = metric.color;
      ctx.fill();
    }
  }, [width, points, start, end, upper, lower, metric, units, hovered, hasData, seconds]);

  return (
    <div className="performance-chart-frame">
      <canvas ref={canvas} aria-label={`${metric.name} chart over ${seconds} seconds`} role="img"
        onPointerMove={(event) => setCursor(Math.max(0, Math.min(1, (event.nativeEvent.offsetX - 50) / (width - 65))))}
        onPointerLeave={() => setCursor(null)} />
      {hovered && <output className="performance-chart-tooltip">{chartTime.format(hovered.timestamp)} · {format(hovered.value, units)}</output>}
      {!hasData && <p className="performance-chart-empty">No readings in this time range.</p>}
    </div>
  );
}

export default function PerformanceDashboard() {
  const [samples, setSamples] = useState([]);
  const [active, setActive] = useState("cpu");
  const [seconds, setSeconds] = useState(60);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    let timer;
    let pending = false;
    async function sample() {
      if (document.hidden || pending) return;
      pending = true;
      try {
        const next = await getPerformanceSnapshot(HOOK_DETECTION_API, controller.signal);
        if (controller.signal.aborted) return;
        setError("");
        setSamples((previous) => previous.at(-1)?.timestamp === next.timestamp
          ? previous
          : [...previous.filter(({timestamp}) => timestamp > next.timestamp - 300_000), next]);
      } catch (failure) {
        if (!controller.signal.aborted) setError(apiErrorMessage(failure, "Could not load detector performance readings."));
      } finally {
        pending = false;
        if (!controller.signal.aborted && !document.hidden) timer = setTimeout(sample, INTERVAL_MS);
      }
    }
    function visibilityChanged() {
      clearTimeout(timer);
      if (!document.hidden) sample();
    }
    sample();
    document.addEventListener("visibilitychange", visibilityChanged);
    return () => {
      controller.abort();
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visibilityChanged);
    };
  }, []);

  const latest = samples.at(-1);
  const end = latest?.timestamp ?? Date.now();
  const metric = METRICS[active];
  const current = latest?.metrics[active];
  const points = samples.filter(({timestamp}) => timestamp > end - seconds * 1000)
    .map(({timestamp, metrics}) => ({timestamp, value: metrics[active]?.value}));
  const values = points.map(({value}) => value).filter(finite);
  const stats = [
    ["Current", current?.value],
    ["Average", values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null],
    ["Peak", values.length ? Math.max(...values) : null],
  ];

  return (
    <>
      <Header />
      <main className="performance-page">
        <header className="workspace-heading performance-heading">
          <div>
            <h1><Activity aria-hidden="true" size={29} /> System Performance</h1>
            <p>ASRock Industrial 4X4-8640U</p>
          </div>
          <div className="workspace-heading-status performance-feed" data-state={error ? "offline" : latest ? "live" : "loading"}>
            <span aria-hidden="true" />
            {error ? "Signal lost" : latest ? "Live · updates every 2s" : "Connecting…"}
          </div>
        </header>
        {error && <p className="performance-error" role="status">{error} {latest && `Last sample at ${chartTime.format(end)}.`}</p>}

        <section className="performance-metrics" aria-label="Hardware metrics">
          {["cpu", "gpu", "temperature"].map((key) => {
            const entry = METRICS[key];
            const reading = latest?.metrics[key];
            const Icon = entry.icon;
            return <button key={key} type="button" className="performance-metric" style={{"--metric-color": entry.color}} aria-pressed={key === active} onClick={() => setActive(key)}>
              <span className="performance-metric-label"><Icon size={18} aria-hidden="true" /> {entry.label}</span>
              <span className="performance-metric-value">{format(reading?.value, entry.unit)}</span>
              <span className="performance-metric-source">{reading ? (finite(reading.value) ? reading.source : "Reading unavailable") : "Waiting for data"}</span>
            </button>;
          })}
        </section>

        <NpuReadouts npu={latest?.metrics.npu} stale={Boolean(error)}
          trendSelected={active === "npu"} onSelectTrend={() => setActive("npu")} />

        <section className="performance-panel" style={{"--metric-color": metric.color}} aria-labelledby="performance-trend">
          <header className="performance-panel-heading">
            <div>
              <h2 id="performance-trend">{metric.name}</h2>
              <p>{current?.source || "Waiting for the first reading"}</p>
            </div>
            <div className="performance-ranges" role="group" aria-label="Chart time range">
              {RANGES.map(([range, label]) => <button key={range} type="button" aria-pressed={range === seconds} onClick={() => setSeconds(range)}>{label}</button>)}
            </div>
          </header>
          <HistoryChart key={active} points={points} end={end} seconds={seconds} metric={metric} units={metric.unit} />
          <dl className="performance-summary">
            {stats.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{format(value, metric.unit)}</dd></div>)}
          </dl>
        </section>
      </main>
    </>
  );
}
