import { useEffect, useState } from "react";
import LiveMap from "./LiveMap";
import type { Snapshot } from "./types";

type Evaluation = {
  generated_at: string;
  metadata: { repeats: number; nodes: number; directed_edges: number };
  comparison: { scenario: string; algorithm: string; median_ms: number; p95_ms: number; nodes_explored: number; travel_time_s: number | null }[];
};

function eta(seconds: number) {
  return `${Math.floor(seconds / 60).toString().padStart(2, "0")}:${Math.round(seconds % 60).toString().padStart(2, "0")}`;
}

function corridorNames(names: string[]) {
  const mainRoads = names.filter((name) => ["Avenue Road", "Valluvar Kottam High Road", "Uttamar Gandhi Salai", "Model School Road", "Khader Nawaz Khan Road"].includes(name));
  return (mainRoads.length ? mainRoads.slice(0, 3) : names.filter((name) => name !== "Unnamed road").slice(0, 3)).join(", ");
}

async function patchBrainControls(changes: Record<string, boolean | number>) {
  await fetch("/api/brain/controls", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
}

export default function BrainPage() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [connected, setConnected] = useState(false);
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/routing/evaluation", { signal: controller.signal })
      .then((response) => response.ok ? response.json() : null)
      .then(setEvaluation).catch(() => {});
    return () => controller.abort();
  }, []);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: number | undefined;
    let disposed = false;
    const connect = () => {
      const protocol = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${protocol}://${location.host}/ws/brain`);
      socket.onopen = () => setConnected(true);
      socket.onmessage = (event) => setSnapshot(JSON.parse(event.data) as Snapshot);
      socket.onclose = () => {
        setConnected(false);
        if (!disposed) retry = window.setTimeout(connect, 1200);
      };
    };
    connect();
    return () => {
      disposed = true;
      if (retry) clearTimeout(retry);
      socket?.close();
    };
  }, []);

  const selectedRoute = snapshot?.routes.find((route) => route.selected);
  const alternateRoutes = snapshot?.routes.filter((route) => !route.selected) ?? [];
  const controls = snapshot?.controls;
  const decision = snapshot?.routing_decision;
  const activeModelCount =
    (controls?.historical_model_enabled ? 1 : 0) + (controls?.spatial_temporal_model_enabled ? 1 : 0);

  return (
    <main className="live-page">
      <header className="live-heading">
        <div className="live-heading-main">
          <p className="overline">
            Forecast-only decision engine <span className="heading-divider" /> No perception or macro feed wired yet
          </p>
          <h1>The brain<span className="period">.</span></h1>
          <p className="heading-subtitle">
            AMB-01 <span /> Route and signal decisions fused from {activeModelCount || "no"} forecasting{" "}
            {activeModelCount === 1 ? "model" : "models"}
          </p>
        </div>
        <div className="heading-eta">
          <span>Estimated arrival</span>
          <strong>{selectedRoute ? eta(selectedRoute.eta_s) : "--:--"}</strong>
          <small>min : sec</small>
        </div>
      </header>

      <div className="live-layout">
        <section className="map-panel" aria-label="Brain-driven route map">
          <LiveMap snapshot={snapshot} />
          <div className="map-coordinate">
            {snapshot ? `${snapshot.vehicle.position.lat.toFixed(4)}° N   ${snapshot.vehicle.position.lon.toFixed(4)}° E` : "13.0620° N   80.2445° E"}
          </div>
          <div className="map-legend">
            <span><i className="selected-line" />Brain-selected route</span>
            <span><i />Alternative</span>
          </div>
          <div className="map-caption"><span>LIVE POSITION</span><b>{snapshot?.vehicle.vehicle_id ?? "AMB-01"}</b></div>
        </section>

        <aside className="context-panel" aria-label="Brain decision context">
          <section className="context-section route-summary">
            <div className="section-label">Selected route <span className="status-mark">Active</span></div>
            <h2>{selectedRoute?.label ?? (decision?.status === "no_path" ? "No open route" : decision?.status === "arrived" ? "Hospital reached" : "Awaiting decision")}</h2>
            {!decision && <p>The brain will pick a route once forecasts arrive.</p>}
            {alternateRoutes.length > 0 && (
              <div className="route-options">
                {alternateRoutes.map((route) => (
                  <div key={route.route_id}><span>{route.label}</span><strong>{route.feasible ? eta(route.eta_s) : "Blocked"}</strong></div>
                ))}
              </div>
            )}
          </section>

          {decision && <section className="context-section routing-explanation" aria-label="Routing decision">
            <div className="section-label">Routing Decision <span className="status-mark">Illustrative inputs</span></div>
            <h2>{decision.decision}</h2>
            <p>{decision.reason}</p>
            <dl>
              <dt>Original corridor</dt><dd>{corridorNames(decision.original_roads) || "Hospital node"}</dd>
              <dt>Incident</dt><dd>{decision.blocked_edge_ids.length ? `Simulated closure: ${decision.blocked_edge_ids.length} directed segments` : controls?.congestion_scenario ? "Synthetic predicted congestion" : "None"}</dd>
              <dt>Selected corridor</dt><dd>{corridorNames(decision.selected_roads) || "No active path"}</dd>
              <dt>Algorithm</dt><dd>{decision.algorithm}</dd>
              <dt>Weights</dt><dd>{decision.weight_source}</dd>
              <dt>Switching margin</dt><dd>At least {decision.minimum_saving_s}s and {decision.minimum_saving_fraction * 100}%</dd>
              <dt>Road connectivity</dt><dd>{decision.connectivity_valid ? "Verified directed road edges" : "No feasible route"}</dd>
              <dt>Search</dt><dd>{decision.nodes_explored} nodes explored in {decision.computation_ms.toFixed(2)} ms</dd>
            </dl>
          </section>}

          <section className="context-section next-signal">
            <div className="section-label">Corridor signals</div>
            {snapshot?.signals.length ? (
              <div className="corridor-line" aria-label="Upcoming intersections">
                {snapshot.signals.map((signal) => (
                  <div className={signal.phase} key={signal.intersection_id}>
                    <i /><b>{signal.intersection_id}</b>
                  </div>
                ))}
              </div>
            ) : (
              <p>Signal timing will appear here.</p>
            )}
          </section>

          <section className="context-section system-readout">
            <div className="section-label">Fusion confidence</div>
            <div>
              <strong>{Math.round((snapshot?.health.overall_confidence ?? 0) * 100)}%</strong>
              <span>{activeModelCount} of 2 models enabled</span>
            </div>
            <p>Perception and macro feed: not wired into the brain yet — forecasting only.</p>
          </section>

          <details className="controls-drawer" open>
            <summary>Brain controls <span>Toggle model inputs live</span></summary>
            <div className="controls-body">
              <button type="button" onClick={() => fetch("/api/brain/reset", { method: "POST" })}>Restart mission</button>
              <Control
                label="Inject simulated road closure"
                checked={controls?.accident ?? false}
                onChange={(value) => patchBrainControls({ accident: value })}
                danger
              />
              <Control
                label="Historical-average forecaster"
                checked={controls?.historical_model_enabled ?? true}
                onChange={(value) => patchBrainControls({ historical_model_enabled: value })}
              />
              <Control
                label="Spatial-temporal forecaster"
                checked={controls?.spatial_temporal_model_enabled ?? true}
                onChange={(value) => patchBrainControls({ spatial_temporal_model_enabled: value })}
              />
              <Control
                label="Inject forecast congestion scenario"
                checked={controls?.congestion_scenario ?? false}
                onChange={(value) => patchBrainControls({ congestion_scenario: value })}
                danger
              />
            </div>
          </details>
        </aside>
      </div>

      <p className="demo-disclosure">
        Research prototype: A* searches the directed OSM graph using fused synthetic baseline forecasts on the
        monitored corridors and static fallback elsewhere. Congestion and closures are simulated. Vehicle motion
        and virtual signal checkpoints are illustrative. D* Lite remains future work. Connection: {connected ? "live" : "reconnecting…"}
      </p>

      <section className="decision-log" aria-label="Decision log">
        <div><span className="section-label">Decision log</span><h2>Recent decisions.</h2></div>
        <ol>
          {snapshot?.decisions.length ? (
            snapshot.decisions.slice(-6).map((event, index) => (
              <li key={`${event.kind}-${event.at_s}-${index}`}>
                <time>{event.at_s.toFixed(1)}s</time>
                <strong>{event.message}</strong>
                <span>{event.kind.replaceAll("_", " ")}</span>
              </li>
            ))
          ) : (
            <li className="empty-log">Decisions will appear as the brain ticks.</li>
          )}
        </ol>
      </section>
      <section className="routing-comparison" aria-label="Measured routing algorithm comparison">
        <div className="section-label">Measured algorithm comparison</div>
        <h2>Routing evaluation</h2>
        {evaluation ? <>
          <p>{evaluation.metadata.repeats} repetitions per algorithm and scenario on {evaluation.metadata.nodes.toLocaleString()} nodes and {evaluation.metadata.directed_edges.toLocaleString()} directed edges. Times are measured on the evaluation computer. Traffic inputs are illustrative.</p>
          <div className="comparison-scroll"><table>
            <thead><tr><th>Scenario</th><th>Algorithm</th><th>Median ms</th><th>p95 ms</th><th>Nodes explored</th><th>Travel cost s</th></tr></thead>
            <tbody>{evaluation.comparison.map((row) => <tr key={`${row.scenario}-${row.algorithm}`}>
              <td>{row.scenario}</td><td>{row.algorithm === "astar" ? "A*" : "Dijkstra"}</td><td>{row.median_ms.toFixed(3)}</td><td>{row.p95_ms.toFixed(3)}</td><td>{row.nodes_explored}</td><td>{row.travel_time_s === null ? "No path" : row.travel_time_s.toFixed(3)}</td>
            </tr>)}</tbody>
          </table></div>
          <p>Both algorithms return equal optimal cost. A* can explore fewer nodes while its heuristic adds computation. D* Lite remains future work.</p>
          <a href="/api/routing/evidence/routing-review.html" target="_blank" rel="noreferrer">Open review evidence with accident overlay and connectivity proof</a>
        </> : <p>Routing evidence is unavailable. Generate the evaluation report to populate this table.</p>}
      </section>
    </main>
  );
}

function Control({ label, checked, onChange, danger = false }: { label: string; checked: boolean; onChange: (value: boolean) => void; danger?: boolean }) {
  return (
    <label className={`control ${danger ? "danger" : ""}`}>
      <span>{label}</span>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <i />
    </label>
  );
}
