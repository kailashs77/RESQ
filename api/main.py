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
    return {
        "fast": {"available": True, "detail": "Built-in deterministic simulator"},
        "sumo": {
            "available": SumoTraCIAdapter("unused.sumocfg").available,
            "detail": "Requires SUMO/TraCI and a .sumocfg network",
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
