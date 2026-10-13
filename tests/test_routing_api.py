import pytest
from httpx import ASGITransport, AsyncClient

from api.main import WEB_DIST, app, brain_simulation
from sim.chennai.roads import demo_plan


@pytest.mark.asyncio
async def test_brain_api_closure_explanation_no_path_and_recovery():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/brain/reset")
        try:
            response = await client.patch(
                "/api/brain/controls",
                json={
                    "historical_model_enabled": False,
                    "spatial_temporal_model_enabled": False,
                    "accident": True,
                },
            )
            assert response.status_code == 200
            snapshot = (await client.get("/api/brain/snapshot")).json()
            assert snapshot["routing_decision"]["connectivity_valid"]
            assert snapshot["incident"] is not None
            active = next(route for route in snapshot["routes"] if route["selected"])
            assert set(active["edge_ids"]).isdisjoint(demo_plan().blocked_edges)
            invalid = await client.patch(
                "/api/brain/controls",
                json={
                    "blocked_edge_ids": ["not-an-edge"],
                },
            )
            assert invalid.status_code == 422
            assert not brain_simulation.controls.blocked_edge_ids
            await client.post("/api/brain/reset")
            start = brain_simulation._engine.plan.normal.nodes[0]
            exits = [edge.edge_id for edge in brain_simulation._engine.network.adjacency[start]]
            await client.patch("/api/brain/controls", json={"blocked_edge_ids": exits})
            stopped = (await client.get("/api/brain/snapshot")).json()
            assert stopped["routing_decision"]["status"] == "no_path"
            assert stopped["routes"] == [] and stopped["vehicle"]["route_id"] is None
            assert stopped["vehicle"]["speed_mps"] == 0
            await client.patch("/api/brain/controls", json={"blocked_edge_ids": []})
            assert (await client.get("/api/brain/snapshot")).json()["routing_decision"][
                "connectivity_valid"
            ]
        finally:
            await client.post("/api/brain/reset")


@pytest.mark.asyncio
async def test_measured_evidence_and_only_allowed_files_are_served():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/routing/evaluation")
        assert response.status_code == 200
        report = response.json()
        assert len(report["comparison"]) == 12
        assert all(row["equal_optimal_cost"] for row in report["comparison"])
        assert all(report["route_changes"].values())
        html = await client.get("/api/routing/evidence/routing-review.html")
        assert html.status_code == 200 and "Proof of valid road connectivity" in html.text
        assert (await client.get("/api/routing/evidence/engine.py")).status_code == 404


@pytest.mark.asyncio
async def test_map_module_workers_have_javascript_mime_type():
    workers = list((WEB_DIST / "assets").glob("maplibre-gl-worker-*.mjs"))
    if not workers:
        pytest.skip("frontend production assets have not been built")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for name in (workers[0].name, "maplibre-gl-shared.mjs"):
            response = await client.get(f"/assets/{name}")
            assert response.status_code == 200
            assert response.headers["content-type"].split(";")[0] == "text/javascript"
