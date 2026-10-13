from experiments.routing_evaluation import evaluate


def test_evaluation_measures_real_queries_and_retains_auditable_proof():
    report, network, _, _ = evaluate(repeats=2)
    assert len(report["raw_samples"]) == 24
    assert len(report["comparison"]) == 12
    assert all(report["route_changes"].values())
    assert all(
        report["dynamic_checks"][key]
        for key in ("closure_overrides_margin", "no_path_emits_no_route")
    )
    for proof in report["connectivity_proofs"].values():
        assert proof["valid"] and proof["edge_count"] + 1 == proof["node_count"]
        for item in proof["edges"]:
            source, edge = network.edge_index[item["edge_id"]]
            assert source == item["source"] and edge.target == item["target"]
            assert (
                item["edge_id"] not in report["closure_edge_ids"]
                if proof is report["connectivity_proofs"]["Accident closure"]
                else True
            )
    assert report["hysteresis"]["without_hysteresis"]["switches"] == 41
    assert report["hysteresis"]["with_hysteresis"]["switches"] == 1
