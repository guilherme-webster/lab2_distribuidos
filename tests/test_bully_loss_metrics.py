"""Contagem de tentativas espúrias e exclusão de ensaios com falhas reais."""

import math
import csv
import json

import pytest

from tests.scripts import ROOT, load_script

metrics = load_script("bully_loss_metrics")


def scenario():
    records = []
    for node in (1, 2, 3):
        records += [
            {"ts": 0, "node": node, "event": "node_start"},
            {"ts": 1, "node": node, "event": "leader_change", "leader": 3},
            {"ts": 6, "node": node, "event": "msg_send"},
        ]
    meta = {"n": 3, "measure_start_ts": 5, "measure_end_ts": 35, "faults_planned": []}
    return records, meta


def election(ts, node, reason="leader_suspected", election_id="attempt"):
    return {"ts": ts, "node": node, "event": "election_start", "reason": reason, "election_id": election_id}


def test_counts_local_attempts_not_global_rounds_and_uses_half_open_window():
    records, meta = scenario()
    records += [election(4, 1), election(5, 1), election(5, 1),
                election(6, 2, "election_received"), election(35, 1, election_id="later")]
    result, _ = metrics.bully_loss_metrics(list(reversed(records)), meta)
    assert result["spurious_elections"] == 2
    assert result["spurious_election_rate"] == 4
    assert result["elections_leader_suspected"] == 1
    assert result["elections_election_received"] == 1
    assert result["violations"] == 0


def test_zero_loss_baseline_and_transient_disagreement_do_not_invalidate_run():
    records, meta = scenario()
    records += [{"ts": 7, "node": 1, "event": "leader_change", "leader": None}]
    result, _ = metrics.bully_loss_metrics(records, meta)
    assert result["spurious_elections"] == 0
    assert result["initial_agreement"] == 1
    assert result["final_agreement"] == 0
    assert result["violations"] == 0


@pytest.mark.parametrize("event", ["node_start", "node_stop", "handler_error", "fault"])
def test_real_faults_or_lifecycle_errors_exclude_counts(event):
    records, meta = scenario()
    records.append({"ts": 7, "node": 1, "event": event})
    result, _ = metrics.bully_loss_metrics(records, meta)
    assert result["violations"] == 1
    assert math.isnan(result["spurious_elections"])


def test_missing_node_and_startup_inside_measurement_invalidate_run():
    records, meta = scenario()
    missing = [r for r in records if r["node"] != 3]
    assert metrics.bully_loss_metrics(missing, meta)[0]["violations"] == 1
    records.append(election(6, 1, "startup"))
    assert metrics.bully_loss_metrics(records, meta)[0]["violations"] == 1


def test_loss_spec_and_analysis_export_without_faults(tmp_path):
    runner = load_script("run_experiment")
    analyze = load_script("analyze")
    spec_path = ROOT / "experiments/configs/bully-loss.toml"
    spec = runner.load_spec(spec_path)
    assert spec.seeds == 10 and not spec.faults
    assert spec.sweep == {"DROP_RATE": [0.0, 0.05, 0.1, 0.2]}
    records, meta = scenario()
    records += [election(7, 1), election(8, 2, "election_received")]
    exp = tmp_path / "raw" / spec.name
    dest = exp / "DROP_RATE=0.1/seed-01"
    dest.mkdir(parents=True)
    (exp / "spec.toml").write_text(spec_path.read_text())
    meta.update(complete=True, config="DROP_RATE=0.1", seed=1, params={**spec.base, "N": 3, "DROP_RATE": 0.1})
    (dest / "meta.json").write_text(json.dumps(meta))
    (dest / "logs.jsonl").write_text("\n".join(json.dumps(r) for r in records))
    (dest / "faults.jsonl").touch()
    out = tmp_path / "results"
    assert analyze.main([str(exp), "--out", str(out), "--no-plots"]) == 0
    with (out / spec.name / "runs.csv").open() as stream:
        row = next(csv.DictReader(stream))
    assert float(row["spurious_elections"]) == 2
    assert float(row["spurious_election_rate"]) == 4
