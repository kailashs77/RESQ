"""Standalone review artifact built from measured routing evidence."""

import csv
import json
from html import escape
from math import cos, radians

from schema import GeoPoint
from sim.chennai.roads import road_geojson


def overlay_svg(network, plan, paths):
    points = plan.normal.geometry + paths["Accident closure"].geometry
    west, east = min(p.lon for p in points), max(p.lon for p in points)
    south, north = min(p.lat for p in points), max(p.lat for p in points)
    ratio = cos(radians((south + north) / 2))
    scale = min(870 / ((east - west) * ratio), 510 / (north - south))

    def xy(p):
        return 65 + (p.lon - west) * ratio * scale, 95 + (north - p.lat) * scale

    def line(points, color, width, extra=""):
        coords = " ".join(f"{xy(p)[0]:.2f},{xy(p)[1]:.2f}" for p in points)
        return (
            f'<polyline points="{coords}" fill="none" stroke="{color}" '
            f'stroke-width="{width}" stroke-linejoin="round" '
            f'stroke-linecap="round" {extra}/>'
        )

    pieces = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 720" '
        'role="img" aria-label="Chennai road graph, closure and connected detour">',
        '<rect width="1000" height="720" fill="#f4f6f2"/>',
        '<g font-family="Segoe UI, Arial" fill="#213b36">',
        '<text x="35" y="35" font-size="22" font-weight="700">'
        "Accident rerouting on mapped Chennai roads</text>",
        '<text x="35" y="59" font-size="13">Simulated closure. '
        "Every detour segment is an open directed OSM edge.</text>",
    ]
    # Clip background streets to the map area, retaining unclipped labels below.
    pieces.append(
        '<defs><clipPath id="map"><rect x="25" y="75" '
        'width="950" height="555"/></clipPath></defs><g clip-path="url(#map)">'
    )
    for feature in road_geojson()["features"]:
        coords = [GeoPoint(lat=lat, lon=lon) for lon, lat in feature["geometry"]["coordinates"]]
        pieces.append(line(coords, "#d6dcd4", 1.3))
    pieces.append(line(plan.normal.geometry, "#7b8796", 6, 'stroke-dasharray="9 6"'))
    pieces.append(line(paths["Accident closure"].geometry, "white", 10))
    pieces.append(line(paths["Accident closure"].geometry, "#13845f", 5))
    for edge_id in sorted(plan.blocked_edges):
        source, edge = network.edge_index[edge_id]
        pieces.append(line((network.nodes[source], network.nodes[edge.target]), "#d9473f", 8))
    pieces.append("</g>")
    for label, point, color, dx, dy in (
        ("Origin", plan.origin, "#213b36", 14, 4),
        ("Hospital", plan.hospital, "#13845f", 14, 4),
        ("Simulated accident", plan.incident, "#d9473f", 14, -14),
    ):
        x, y = xy(point)
        pieces.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="7" fill="{color}" '
            'stroke="white" stroke-width="2"/>'
        )
        pieces.append(
            f'<text x="{x + dx:.2f}" y="{y + dy:.2f}" font-size="14" '
            f'font-weight="600" paint-order="stroke" stroke="#f4f6f2" '
            f'stroke-width="4">{label}</text>'
        )
    for i, (label, color, dash) in enumerate(
        (
            ("Original shortest route", "#7b8796", 'stroke-dasharray="9 6"'),
            ("Open connected detour", "#13845f", ""),
            ("Closed road segments", "#d9473f", ""),
        )
    ):
        x = 35 + i * 305
        pieces.append(
            f'<line x1="{x}" y1="657" x2="{x + 33}" y2="657" '
            f'stroke="{color}" stroke-width="5" {dash}/>'
            f'<text x="{x + 43}" y="662" font-size="14">{label}</text>'
        )
    pieces.extend(
        [
            '<text x="35" y="697" font-size="12">Map data © OpenStreetMap contributors, '
            "ODbL 1.0. Geometry follows graph edges. Turn restrictions are not modelled.</text>",
            "</g></svg>",
        ]
    )
    return "\n".join(pieces)


def write_report(destination, report, network, plan, paths):
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "routing-evaluation.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    with (destination / "algorithm-comparison.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(report["comparison"][0]))
        writer.writeheader()
        writer.writerows(report["comparison"])
    svg = overlay_svg(network, plan, paths)
    (destination / "accident-route-overlay.svg").write_text(svg, encoding="utf-8")
    proof = report["connectivity_proofs"]["Accident closure"]
    rows = []
    for row in report["comparison"]:
        cost = f"{row['travel_time_s']:.3f}" if row["travel_time_s"] is not None else "No path"
        result = "Equal optimal cost" if row["travel_time_s"] is not None else "No invalid route"
        rows.append(
            f"<tr><td>{escape(row['scenario'])}</td><td>"
            f"{'A*' if row['algorithm'] == 'astar' else 'Dijkstra'}</td>"
            f"<td>{row['median_ms']:.3f}</td><td>{row['p95_ms']:.3f}</td>"
            f"<td>{row['nodes_explored']}</td><td>{cost}</td><td>{result}</td></tr>"
        )
    proof_rows = "".join(
        f"<tr><td>{e['index']}</td><td>{e['edge_id']}</td><td>{e['source']}</td>"
        f"<td>{e['target']}</td><td>{escape(e['road'])}</td><td>Open directed edge</td></tr>"
        for e in proof["edges"]
    )
    h, meta = report["hysteresis"], report["metadata"]
    reason = report["dynamic_checks"]["closure"]
    normal_names = ", ".join(dict.fromkeys(plan.normal.names))
    content = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RESQ routing evaluation</title><style>
body{{margin:0;background:#f4f6f2;color:#203b36;font:16px/1.6 "Segoe UI",Arial,sans-serif}}
main{{max-width:1150px;margin:auto;padding:42px 28px}}
h1{{font-size:42px;line-height:1.15;margin:8px 0 18px}}h2{{font-size:25px;margin-top:38px}}
.eyebrow{{color:#13845f;font-size:13px;font-weight:700;letter-spacing:2px}}
.note{{padding:18px 22px;border-left:4px solid #13845f;background:white}}
.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:14px;background:white}}
th,td{{padding:11px 12px;border-bottom:1px solid #dfe5dc;text-align:left;white-space:nowrap}}
th{{background:#203b36;color:white}}svg{{width:100%;border:1px solid #dfe5dc}}
a{{color:#13845f}}code{{background:#e6ebe3;padding:2px 5px}}
details{{background:white;padding:18px;margin-top:20px}}dt{{font-weight:700}}dd{{margin:0 0 12px}}
footer{{font-size:13px;margin-top:40px;color:#60736b}}
@media print{{body{{background:white}}main{{padding:10px}}h2{{break-after:avoid}}
svg{{break-inside:avoid}}details:not([open]){{display:none}}}}
</style><main><div class="eyebrow">RESQ / PRIORITY 1 / EXPERIMENTAL EVIDENCE</div>
<h1>Routing evaluation and dynamic replanning</h1>
<p>Measured Dijkstra and A* on the checked-in Chennai graph:
<b>{meta["nodes"]:,} nodes and {meta["directed_edges"]:,} directed edges</b>.</p>
<div class="note">Computation times are actual measurements on this computer. Congestion,
closures, driving speeds and vehicle motion are controlled illustrative inputs. This report
does not establish real ambulance, Graph WaveNet or SUMO performance.
D* Lite remains future work.</div>
<h2>Algorithm comparison</h2><p>{meta["repeats"]} timed repetitions per scenario and algorithm
after 5 warmups. Seed 77 shuffles algorithm order. Every query verifies equal optimal cost,
and every returned route passes directed connectivity checks.</p>
<div class="scroll"><table><thead><tr><th>Scenario</th><th>Algorithm</th><th>Median ms</th>
<th>p95 ms</th><th>Nodes explored</th><th>Travel cost s</th><th>Result</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
<p>Nodes explored counts non-stale heap expansions including the goal. Timing includes
heuristic preparation, query and path reconstruction. It excludes graph loading, forecast
fitting and validation. Travel cost is an assumed or predicted sum, not measured journey duration.
Raw search times and all samples appear in JSON.</p>
<h2>Accident overlay and routing explanation</h2>{svg}<dl>
<dt>Original corridor</dt><dd>{escape(normal_names)}</dd>
<dt>Incident</dt><dd>Simulated closure of {len(plan.blocked_edges)} directed segments.
Both mapped directions of the affected physical section close.</dd>
<dt>Decision</dt><dd>{escape(reason["decision"])}. {escape(reason["reason"])}</dd>
<dt>Selected detour</dt><dd>{escape(", ".join(reason["selected_roads"]))}</dd>
<dt>Algorithm</dt><dd>A* shortest path using static weights for this closure example.
The core also supports FIFO edge-entry-time costs. The Brain dashboard uses immutable
forecast weight snapshots.</dd></dl>
<p>The shortest static path costs {plan.normal.travel_s:.3f}s. The open detour costs
{paths["Accident closure"].travel_s:.3f}s. It is slower under clear-road assumptions but
becomes necessary after closure. Feasibility overrides the 10s/5% saving threshold.</p>
<h2>Proof of valid road connectivity</h2><div class="note">PASS: {proof["edge_count"]} open
directed edges, {proof["node_count"]} graph nodes and {proof["geometry_count"]} geometry points.
Start {proof["start"]}, hospital {proof["goal"]}. Zero blocked segments in the detour.
Each segment's target equals the next segment's source.</div>
<p>Validation checks actual adjacency, edge identity, mapped direction, endpoints and geometry.
This proves connectivity in the checked-in extract. Turn restrictions, lane rules and
legal/emergency access permissions still require validation.</p>
<details><summary>Inspect every detour edge ({proof["edge_count"]} segments)</summary>
<div class="scroll"><table><tr><th>#</th><th>Edge ID</th><th>Source node</th><th>Target node</th>
<th>Road</th><th>Check</th></tr>{proof_rows}</table></div></details>
<h2>Predictions, hysteresis and graceful failure</h2>
<p>Implemented baseline forecast output is fused and used as graph weights in the congestion case.
The path changes: <b>{report["route_changes"]["forecast_changes_path"]}</b>.
A separate FIFO experiment evaluates corridor costs at each edge's entry time.
Changing departure from 0s to 600s changes the route:
<b>{report["route_changes"]["departure_changes_path"]}</b>.</p>
<p>For {h["samples"]} synthetic decision samples (40 noisy near ties followed by 5 sustained
improvements), switching drops from <b>{h["without_hysteresis"]["switches"]}</b> without hysteresis
to <b>{h["with_hysteresis"]["switches"]}</b> with both margins. Retaining the route adds
{h["with_hysteresis"]["total_retained_extra_cost_s"]:.0f}s summed across illustrative decisions,
a stability/optimality tradeoff rather than a trip delay.</p>
<p>Closing all origin exits returns <code>no_path</code>, an empty active route list,
no vehicle route ID and zero speed. Mid-edge replanning retains the committed directed edge
and searches from its next node. If that edge closes, the vehicle stops at its existing
position. Removing closures allows a connected route to resume.</p>
<h2>Reproduce and demonstrate</h2>
<p>Run <code>uv run python -m experiments.routing_evaluation --repeats 100</code>
from the repository. Open the dashboard's <b>Brain</b> page and toggle simulated closure
or forecast congestion. Routing Decision shows the reason, roads, weights, algorithm,
switching margins, search counts and connectivity. Live/Replay remains a staged presentation.</p>
<p>Supporting files: <a href="algorithm-comparison.csv">comparison CSV</a>,
<a href="routing-evaluation.json">raw results and edge proof</a>,
<a href="accident-route-overlay.svg">exportable overlay</a>.</p>
<footer>Generated {report["generated_at"]} (Asia/Kolkata). Python {meta["python"]}.
{escape(meta["platform"])}. Graph SHA-256: {meta["road_graph_sha256"]}.
Exact source fingerprints and base commit appear in JSON.</footer></main></html>"""
    (destination / "routing-review.html").write_text(content, encoding="utf-8")
