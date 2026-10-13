"""FastAPI entrypoint for the first ResQ vertical slice."""

import asyncio
import json
import os
from pathlib import Path
from typing import Annotated

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from adapters import CarlaSensorRig, SumoTraCIAdapter
from brain import BrainSimulation
from experiments.runner import FastScenarioRunner
from forecast.runtime import (
    HORIZONS_MIN,
    benchmark_reports,
    corridor_forecaster,
    segment_geometry,
)
from recorder import RunStore
from recorder.replay import build_ghost_replay
from schema import (
    BaselineId,
    ControlState,
    ExperimentRequest,
    GhostReplay,
    NetworkSnapshot,
    ResultsSummary,
    ScenarioName,
)
from sim import DemoSimulation
from sim.chennai.roads import road_geojson

app = FastAPI(
    title="ResQ API",
    version="0.1.0",
    description="Predictive emergency routing and green-corridor research API",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

simulation = DemoSimulation()
brain_simulation = BrainSimulation()
ROUTING_EVIDENCE = Path(__file__).resolve().parents[1] / "docs" / "routing-evaluation"
runner = FastScenarioRunner()
store = RunStore()


def ensure_reference_results() -> None:
    if store.count() == 0:
        store.save_many(runner.matrix(list(BaselineId), list(ScenarioName), seeds=5))


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "adapter": "demo", "schema_version": "1.0"}


@app.get("/api/adapters")
def adapter_status() -> dict[str, dict[str, str | bool]]:
    sumo_available = SumoTraCIAdapter("sim/chennai/chennai.sumocfg").available
    return {
        "fast": {"available": True, "detail": "Built-in deterministic simulator"},
        "sumo": {
            "available": sumo_available,
            "detail": (
                "SUMO/TraCI ready; launch with scripts/sumo.ps1"
                if sumo_available
                else "Install the SUMO extra and generate the Chennai network"
            ),
        },
        "carla": {
            "available": CarlaSensorRig().available,
            "detail": "Requires a running CARLA 0.9.x server and Python API",
        },
        "google_routes": {
            "available": bool(os.getenv("GOOGLE_ROUTES_API_KEY")),
            "detail": "Runtime-only; API key is never exposed to the browser",
        },
    }


@app.get("/api/snapshot", response_model=NetworkSnapshot)
def snapshot() -> NetworkSnapshot:
    return simulation.snapshot()


@app.get("/api/roads")
def roads() -> dict:
    """Inspect the same OSM road ways used by the demonstration router."""
    return road_geojson()


@app.get("/api/routing/evaluation")
def routing_evaluation() -> dict:
    """Measured, saved evidence. Do not run a benchmark in a live request."""
    path = ROUTING_EVIDENCE / "routing-evaluation.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Generate routing evaluation evidence first")
    report = json.loads(path.read_text(encoding="utf-8"))
    return {
        key: report[key]
        for key in (
            "generated_at",
            "metadata",
            "comparison",
            "hysteresis",
            "route_changes",
            "dynamic_checks",
        )
    }


@app.get("/api/routing/evidence/{filename}")
def routing_evidence(filename: str) -> FileResponse:
    allowed = {
        "routing-review.html",
        "routing-evaluation.json",
        "algorithm-comparison.csv",
        "accident-route-overlay.svg",
    }
    if filename not in allowed or not (ROUTING_EVIDENCE / filename).is_file():
        raise HTTPException(status_code=404, detail="Evidence file not found")
    return FileResponse(ROUTING_EVIDENCE / filename)


@app.patch("/api/controls", response_model=ControlState)
def update_controls(
    changes: Annotated[dict[str, bool | int], Body(examples=[{"accident": True}])],
) -> ControlState:
    try:
        return simulation.update_controls(**changes)
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/reset", response_model=ControlState)
def reset() -> ControlState:
    simulation.reset()
    return simulation.controls


@app.post("/api/experiments/run", response_model=ResultsSummary)
def run_experiments(request: ExperimentRequest) -> ResultsSummary:
    metrics = runner.matrix(
        request.baselines,
        request.scenarios,
        request.seeds,
        request.adoption_percent,
    )
    store.save_many(metrics)
    return store.summary()


@app.get("/api/results", response_model=ResultsSummary)
def results() -> ResultsSummary:
    ensure_reference_results()
    return store.summary()


@app.get("/api/replays")
def replays() -> list[dict[str, str | int]]:
    return [
        {
            "replay_id": "staged-incident-7",
            "title": "Staged incident road-network comparison",
            "scenario": ScenarioName.HIDDEN_ACCIDENT.value,
            "seed": 7,
        }
    ]


@app.get("/api/replays/ghost", response_model=GhostReplay)
def ghost_replay(seed: int = 7) -> GhostReplay:
    if seed < 0:
        raise HTTPException(status_code=422, detail="seed must be non-negative")
    return build_ghost_replay(seed)


@app.get("/api/forecast")
def forecast_model_card() -> dict:
    """Describe the deployed corridor checkpoint and the evidence behind it."""
    forecaster = corridor_forecaster()
    if forecaster is None:
        return {
            "available": False,
            "detail": (
                "No corridor checkpoint. Build the simulated corridor dataset with "
                "sim/chennai/forecast_dataset.py, then train CHENNAI-SIM."
            ),
            "benchmarks": benchmark_reports(),
        }
    return {"available": True, **forecaster.model_card(), "benchmarks": benchmark_reports()}


@app.get("/api/forecast/segments")
def forecast_segments(horizon: int = 15) -> dict:
    """The current multi-horizon speed forecast for every monitored segment."""
    forecaster = corridor_forecaster()
    if forecaster is None:
        raise HTTPException(status_code=503, detail="no corridor checkpoint is loaded")
    if horizon not in HORIZONS_MIN:
        raise HTTPException(status_code=422, detail=f"horizon must be one of {HORIZONS_MIN}")
    state = forecaster.state()
    return {
        "at": state.at.isoformat(),
        "replay_step": state.step,
        "model": state.model,
        "source": state.source,
        "horizon_minutes": horizon,
        "segments": [
            {
                "segment_id": segment.segment_id,
                "osm_way_id": segment.osm_way_id,
                "name": segment.name,
                "length_m": round(segment.length_m, 1),
                "observed_mph": round(segment.observed_mph, 2),
                "forecast_mph": round(segment.speeds_mph[horizon], 2),
                "stddev_mph": round(segment.stddev_mph[horizon], 2),
                "actual_mph": (
                    round(segment.actual_mph[horizon], 2)
                    if segment.actual_mph[horizon] is not None
                    else None
                ),
                "free_flow_mph": round(segment.free_flow_mph, 2),
                "congestion": round(
                    1 - min(1.0, segment.speeds_mph[horizon] / max(segment.free_flow_mph, 1)), 3
                ),
                "on_route": segment.on_route,
            }
            for segment in state.segments
        ],
    }


@app.get("/api/forecast/geometry")
def forecast_geometry() -> dict:
    """Monitored-segment shapes, so the map can colour the corridor by prediction."""
    geometry = segment_geometry()
    if not geometry:
        raise HTTPException(status_code=503, detail="monitored segments are not built")
    return {"type": "FeatureCollection", "features": list(geometry.values())}


@app.get("/api/forecast/series/{segment_id:path}")
def forecast_series(segment_id: str) -> dict:
    """Recent history, the live forecast, and the held-out truth it is judged against."""
    forecaster = corridor_forecaster()
    if forecaster is None:
        raise HTTPException(status_code=503, detail="no corridor checkpoint is loaded")
    try:
        return forecaster.series(segment_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"unknown segment {segment_id}") from error


@app.get("/api/forecast/benchmarks")
def forecast_benchmarks() -> list[dict]:
    """Measured public-benchmark results for the model class, against required baselines."""
    return benchmark_reports()


@app.websocket("/ws/live")
async def live(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            await websocket.send_text(simulation.snapshot().model_dump_json())
            await asyncio.sleep(0.2)
    except WebSocketDisconnect:
        return


@app.get("/api/brain/snapshot", response_model=NetworkSnapshot)
def brain_snapshot() -> NetworkSnapshot:
    return brain_simulation.snapshot()


@app.patch("/api/brain/controls", response_model=ControlState)
def update_brain_controls(
    changes: Annotated[
        dict[str, bool | int | list[str]], Body(examples=[{"congestion_scenario": True}])
    ],
) -> ControlState:
    try:
        return brain_simulation.update_controls(**changes)
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/brain/reset", response_model=ControlState)
def reset_brain() -> ControlState:
    brain_simulation.reset()
    return brain_simulation.controls


@app.websocket("/ws/brain")
async def brain_live(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            await websocket.send_text(brain_simulation.snapshot().model_dump_json())
            await asyncio.sleep(0.2)
    except WebSocketDisconnect:
        return


WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def single_page_app(path: str) -> FileResponse:
        requested = WEB_DIST / path
        return FileResponse(requested if requested.is_file() else WEB_DIST / "index.html")
