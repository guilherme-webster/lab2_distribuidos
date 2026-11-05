"""Medições de eleição sobre logs controlados e exportação CSV."""

import csv
import json
import math

import pytest

from tests.scripts import ROOT, load_script

metrics = load_script("bully_metrics")
analyze = load_script("analyze")
runner = load_script("run_experiment")
SPEC = ROOT / "experiments/configs/bully-election.toml"


def scenario():
    records = [
        {"ts": 90, "node": node, "event": "leader_change", "leader": 3}
        for node in (1, 2, 3)
    ]
    records += [
        {"ts": 101.3, "event": "fault", "action": "kill", "target": 3, "issued_ts": 101},
        {"ts": 101.5, "node": 1, "event": "election_start"},
        {"ts": 101.6, "node": 2, "event": "election_start"},
        {"ts": 102.1, "node": 2, "event": "leader_change", "leader": 2},
        {"ts": 102.2, "node": 1, "event": "leader_change", "leader": 2},
    ]
    meta = {"n": 3, "measure_start_ts": 100, "measure_end_ts": 108}
    return records, meta


def test_latency_uses_first_election_and_last_survivor_agreement():
    records, meta = scenario()
    result, samples = metrics.bully_metrics(list(reversed(records)), meta)
    assert result["failover_latency_s"] == pytest.approx(1.2)
    assert result["election_latency_s"] == pytest.approx(0.7)
    assert result["detection_to_election_s"] == pytest.approx(0.5)
    assert result["fault_completed_ts"] == 101.3
    assert result["initial_agreement"] == result["converged"] == 1
    assert result["violations"] == 0
    assert samples == {}


def test_transient_agreement_is_not_final_convergence():
    records, meta = scenario()
    records += [
        {"ts": 103, "node": 1, "event": "leader_change", "leader": None},
        {"ts": 104, "node": 1, "event": "leader_change", "leader": 2},
        {"ts": 109, "node": 1, "event": "leader_change", "leader": None},
    ]
    result, _ = metrics.bully_metrics(records, meta)
    assert result["agreement_ts"] == 104
    assert result["election_latency_s"] == pytest.approx(2.5)


@pytest.mark.parametrize("failure", ["no_initial_agreement", "no_convergence", "restart", "handler_error", "no_fault"])
def test_invalid_runs_do_not_produce_latency_samples(failure):
    records, meta = scenario()
    if failure == "no_initial_agreement":
        records = [r for r in records if not (r.get("node") == 1 and r["ts"] == 90)]
    elif failure == "no_convergence":
        records.append({"ts": 103, "node": 1, "event": "leader_change", "leader": None})
    elif failure == "restart":
        records.append({"ts": 103, "node": 1, "event": "node_start"})
    elif failure == "handler_error":
        records.append({"ts": 103, "node": 1, "event": "handler_error"})
    else:
        records = [r for r in records if r["event"] != "fault"]
    result, _ = metrics.bully_metrics(records, meta)
    assert result["violations"] == 1
    assert math.isnan(result["election_latency_s"])
    assert analyze.describe([math.nan, 1.0])["n"] == 1
    assert analyze.describe([math.nan])["n"] == 0


def test_spec_covers_requested_sizes_with_ten_repetitions():
    spec = runner.load_spec(SPEC)
    runs = runner.plan(spec, ROOT / "experiments/raw")
    assert len(runs) == 60
    assert [run.n for run in runs[:6]] == [3, 5, 7, 9, 11, 13]
    assert all(sum(run.n == n for run in runs) == 10 for n in (3, 5, 7, 9, 11, 13))


def test_export_is_readable_as_one_row_per_run(tmp_path):
    spec = runner.load_spec(SPEC)
    records, meta = scenario()
    exp_dir = tmp_path / "raw" / spec.name
    run_dir = exp_dir / "N=3/seed-01"
    run_dir.mkdir(parents=True)
    (exp_dir / "spec.toml").write_text(SPEC.read_text())
    meta.update(complete=True, config="N=3", seed=1, params={**spec.base, "N": 3})
    (run_dir / "meta.json").write_text(json.dumps(meta))
    (run_dir / "logs.jsonl").write_text("\n".join(json.dumps(r) for r in records if r["event"] != "fault"))
    (run_dir / "faults.jsonl").write_text(json.dumps(next(r for r in records if r["event"] == "fault")))
    out = tmp_path / "results"
    assert analyze.main([str(exp_dir), "--out", str(out), "--no-plots"]) == 0
    with (out / spec.name / "runs.csv").open() as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 1
    assert int(rows[0]["N"]) == 3
    assert int(rows[0]["seed"]) == 1
    assert float(rows[0]["election_latency_s"]) == pytest.approx(0.7)
