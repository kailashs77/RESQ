import { useEffect, useMemo, useState } from "react";
import ForecastMap from "./ForecastMap";
import type { ForecastCard, ForecastSegments, ForecastSeries } from "./types";

const HORIZONS = [5, 15, 30, 60];

function mae(horizons: Record<string, { mae_mph: number }> | undefined, step: string) {
  const value = horizons?.[step]?.mae_mph;
  return value === undefined ? "—" : value.toFixed(2);
}

export default function ForecastPage() {
  const [card, setCard] = useState<ForecastCard | null>(null);
  const [segments, setSegments] = useState<ForecastSegments | null>(null);
  const [series, setSeries] = useState<ForecastSeries | null>(null);
  const [horizon, setHorizon] = useState(15);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    // Training may finish while this page is open, so keep asking until it does.
    const pull = () => fetch("/api/forecast")
      .then((response) => response.json())
      .then((data: ForecastCard) => {
        if (!alive) return;
        setCard(data);
        if (!data.available) timer = window.setTimeout(pull, 15000);
      })
      .catch(() => { if (alive) timer = window.setTimeout(pull, 15000); });
    pull();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, []);

  useEffect(() => {
    if (!card?.available) return;
    let alive = true;
    const pull = () => fetch(`/api/forecast/segments?horizon=${horizon}`)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => { if (alive && data) setSegments(data); })
      .catch(() => undefined);
    pull();
    const timer = window.setInterval(pull, 5000);
    return () => { alive = false; clearInterval(timer); };
  }, [card?.available, horizon]);

  const routeSegments = useMemo(
    () => segments?.segments.filter((item) => item.on_route) ?? [],
    [segments],
  );
  const activeId = selected ?? routeSegments[0]?.segment_id ?? segments?.segments[0]?.segment_id ?? null;

  useEffect(() => {
    if (!activeId) return;
    let alive = true;
    const pull = () => fetch(`/api/forecast/series/${encodeURIComponent(activeId)}`)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => { if (alive && data) setSeries(data); })
      .catch(() => undefined);
    pull();
    const timer = window.setInterval(pull, 5000);
    return () => { alive = false; clearInterval(timer); };
  }, [activeId]);

  if (!card) return <main className="state-page"><h1>Reading the model.</h1><p>Loading the corridor forecasting checkpoint.</p></main>;

  if (!card.available) {
    return (
      <main className="state-page forecast-missing">
        <h1>No corridor checkpoint<span className="period">.</span></h1>
        <p>{card.detail}</p>
        <ol className="build-steps">
          <li><code>python -m sim.chennai.forecast_dataset --days 21 --segments 120</code><span>Simulate the study area and record five-minute segment speeds.</span></li>
          <li><code>python -m forecast.training.cli train CHENNAI-SIM</code><span>Fit the Graph WaveNet corridor checkpoint and calibrate its intervals.</span></li>
        </ol>
        {card.benchmarks.length > 0 && <BenchmarkTable benchmarks={card.benchmarks} />}
      </main>
    );
  }

  const worst = [...(segments?.segments ?? [])].sort((a, b) => b.congestion - a.congestion).slice(0, 6);
  const averaged = routeSegments.length ? routeSegments : (segments?.segments ?? []);
  const corridorMean = averaged.length
    ? averaged.reduce((total, item) => total + item.forecast_mph, 0) / averaged.length
    : null;

  return (
    <main className="forecast-page">
      <header className="page-intro">
        <div>
          <span className="overline">Trained traffic forecasting <span className="heading-divider" /> Graph WaveNet</span>
          <h1>The corridor, five to sixty minutes ahead<span className="period">.</span></h1>
          <p>{card.model} trained on {card.trained_on}. The demonstration replays the {card.replay_split}, so every prediction shown is out-of-sample and the truth it is judged against is known.</p>
        </div>
        <div className="horizon-picker" role="group" aria-label="Forecast horizon">
          {HORIZONS.map((item) => (
            <button type="button" key={item} className={horizon === item ? "active" : ""} onClick={() => setHorizon(item)}>{item}<small>min</small></button>
          ))}
        </div>
      </header>

      <section className="forecast-strip">
        <div><span>Monitored segments</span><strong>{card.segments}</strong><small>simulated corridor sensors</small></div>
        <div><span>Corridor mean at +{horizon} min</span><strong>{corridorMean ? corridorMean.toFixed(1) : "—"}<i>mph</i></strong><small>{routeSegments.length ? `${routeSegments.length} segments on the ambulance route` : `${averaged.length} monitored segments`}</small></div>
        <div><span>Held-out MAE at +{horizon} min</span><strong>{mae(card.test_report, String(horizon))}<i>mph</i></strong><small>test split, never trained on</small></div>
        <div><span>90% interval coverage</span><strong>{card.coverage_90 != null ? `${(card.coverage_90 * 100).toFixed(1)}%` : "—"}</strong><small>conformal band, target 90%</small></div>
      </section>

      <div className="forecast-layout">
        <section className="map-panel" aria-label="Predicted corridor speeds">
          <ForecastMap segments={segments} selected={activeId} onSelect={setSelected} />
          <div className="map-legend forecast-legend">
            <span><i style={{ background: "#2a9b37" }} />Free flowing</span>
            <span><i style={{ background: "#d8a222" }} />Slowing</span>
            <span><i style={{ background: "#c0392b" }} />Congested</span>
          </div>
          <div className="map-caption"><span>PREDICTED +{horizon} MIN</span><b>{segments?.at ? new Date(segments.at).toUTCString().slice(17, 22) : "--:--"} sim clock</b></div>
        </section>

        <aside className="context-panel" aria-label="Forecast detail">
          <section className="context-section">
            <div className="section-label">Predicted against actual</div>
            <h2>{series?.name || activeId || "Select a segment"}</h2>
            <SeriesChart series={series} />
            <p>Solid line is the recorded speed the model was given. The band is the calibrated 90% interval around its prediction; the dotted line is what actually happened in the held-out simulation.</p>
          </section>

          <section className="context-section">
            <div className="section-label">Slowest predicted links</div>
            <div className="segment-list">
              {worst.map((item) => (
                <button type="button" key={item.segment_id} className={item.segment_id === activeId ? "active" : ""} onClick={() => setSelected(item.segment_id)}>
                  <span>{item.name || item.segment_id}</span>
                  <strong>{item.forecast_mph.toFixed(1)}<i>mph</i></strong>
                  <small>{item.free_flow_mph.toFixed(0)} mph free flow{item.on_route ? " · on route" : ""}</small>
                </button>
              ))}
            </div>
          </section>

          <section className="context-section model-card">
            <div className="section-label">Model card</div>
            <dl>
              <div><dt>Input window</dt><dd>{card.input_minutes} min</dd></div>
              <div><dt>Best epoch</dt><dd>{card.best_epoch ?? "—"}</dd></div>
              <div><dt>Validation MAE</dt><dd>{card.best_validation_mae_mph?.toFixed(3) ?? "—"} mph</dd></div>
              <div><dt>Trained on</dt><dd>{card.trained_device ?? "—"}</dd></div>
              <div><dt>PyTorch</dt><dd>{card.torch_version ?? "—"}</dd></div>
              <div><dt>Revision</dt><dd>{card.git_revision?.slice(0, 8) ?? "—"}</dd></div>
            </dl>
          </section>
        </aside>
      </div>

      <section className="forecast-compare">
        <div className="chart-intro">
          <div><span className="section-label">Against the required baselines</span><h2>Does the model beat doing nothing?</h2></div>
          <p>Mean absolute error in mph on the held-out corridor test split. Persistence repeats the last reading; the historical average uses the training weekly profile.</p>
        </div>
        <div className="table-scroll">
          <table>
            <thead><tr><th>Predictor</th>{HORIZONS.map((item) => <th key={item}>+{item} min</th>)}</tr></thead>
            <tbody>
              <tr className="resq-row"><td><b>Graph WaveNet</b></td>{HORIZONS.map((item) => <td key={item}>{mae(card.test_report, String(item))}</td>)}</tr>
              <tr><td>Persistence</td>{HORIZONS.map((item) => <td key={item}>{mae(card.test_baselines?.persistence, String(item))}</td>)}</tr>
              <tr><td>Historical average</td>{HORIZONS.map((item) => <td key={item}>{mae(card.test_baselines?.historical_average, String(item))}</td>)}</tr>
            </tbody>
          </table>
        </div>
      </section>

      {card.benchmarks.length > 0 && <BenchmarkTable benchmarks={card.benchmarks} />}

      <p className="demo-disclosure">{card.limitation}</p>
    </main>
  );
}

function BenchmarkTable({ benchmarks }: { benchmarks: ForecastCard["benchmarks"] }) {
  return (
    <section className="forecast-compare">
      <div className="chart-intro">
        <div><span className="section-label">Public benchmarks</span><h2>The same model class, on measured sensors.</h2></div>
        <p>Trained and evaluated here on the standard chronological split. These are Californian freeway loop detectors: they validate the method, not Chennai accuracy.</p>
      </div>
      <div className="table-scroll">
        <table>
          <thead><tr><th>Dataset</th><th>Seed</th>{HORIZONS.map((item) => <th key={item}>+{item} min MAE</th>)}<th>Persistence +15</th><th>Coverage</th></tr></thead>
          <tbody>
            {benchmarks.map((item) => (
              <tr key={`${item.dataset}-${item.seed}`}>
                <td><b>{item.dataset}</b></td>
                <td>{item.seed}</td>
                {HORIZONS.map((step) => <td key={step}>{mae(item.model, String(step))}</td>)}
                <td>{mae(item.baselines?.persistence, "15")}</td>
                <td>{item.coverage_90 != null ? `${(item.coverage_90 * 100).toFixed(1)}%` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function SeriesChart({ series }: { series: ForecastSeries | null }) {
  if (!series) return <div className="series-chart empty">Waiting for the replay clock.</div>;
  const history = series.history.slice(-24);
  const forecast = series.forecast;
  const points = history.length + forecast.length;
  const values = [
    ...history.map((item) => item.speed_mph),
    ...forecast.flatMap((item) => [item.low_mph, item.high_mph, item.actual_mph ?? item.forecast_mph]),
  ];
  const top = Math.max(series.free_flow_mph, ...values) * 1.05;
  const bottom = 0;
  const width = 520;
  const height = 190;
  const x = (index: number) => (index / Math.max(points - 1, 1)) * width;
  const y = (value: number) => height - ((value - bottom) / Math.max(top - bottom, 1)) * height;

  const historyPath = history.map((item, index) => `${index ? "L" : "M"}${x(index).toFixed(1)} ${y(item.speed_mph).toFixed(1)}`).join(" ");
  const bridge = history.length && forecast.length
    ? `M${x(history.length - 1).toFixed(1)} ${y(history.at(-1)!.speed_mph).toFixed(1)} L${x(history.length).toFixed(1)} ${y(forecast[0].forecast_mph).toFixed(1)}`
    : "";
  const forecastPath = forecast.map((item, index) => `${index ? "L" : "M"}${x(history.length + index).toFixed(1)} ${y(item.forecast_mph).toFixed(1)}`).join(" ");
  const actualPath = forecast
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => item.actual_mph != null)
    .map(({ item, index }, position) => `${position ? "L" : "M"}${x(history.length + index).toFixed(1)} ${y(item.actual_mph!).toFixed(1)}`)
    .join(" ");
  const band = [
    ...forecast.map((item, index) => `${index ? "L" : "M"}${x(history.length + index).toFixed(1)} ${y(item.high_mph).toFixed(1)}`),
    ...[...forecast].reverse().map((item, index) => `L${x(history.length + forecast.length - 1 - index).toFixed(1)} ${y(item.low_mph).toFixed(1)}`),
    "Z",
  ].join(" ");

  return (
    <div className="series-chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Recorded speed, forecast band and actual outcome">
        <line x1="0" y1={y(series.free_flow_mph)} x2={width} y2={y(series.free_flow_mph)} className="free-flow-line" />
        <line x1={x(history.length - 0.5)} y1="0" x2={x(history.length - 0.5)} y2={height} className="now-line" />
        <path d={band} className="band" />
        <path d={historyPath} className="history" />
        {bridge && <path d={bridge} className="forecast" />}
        <path d={forecastPath} className="forecast" />
        {actualPath && <path d={actualPath} className="actual" />}
      </svg>
      <div className="series-legend">
        <span><i className="history" />Observed</span>
        <span><i className="forecast" />Forecast</span>
        <span><i className="actual" />Actual</span>
        <span><i className="free-flow" />Free flow {series.free_flow_mph.toFixed(0)} mph</span>
      </div>
    </div>
  );
}
