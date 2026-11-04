import csv
import gzip
import json
import math

import pytest

from tests.scripts import load_script

an = load_script("analyze")


# --- estatística ------------------------------------------------------------

def test_percentile_matches_linear_interpolation():
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert an.percentile(values, 50) == pytest.approx(5.5)
    assert an.percentile(values, 95) == pytest.approx(9.55)
    assert an.percentile(values, 99) == pytest.approx(9.91)
    assert an.percentile([3.0], 99) == 3.0
    assert math.isnan(an.percentile([], 50))


def test_describe():
    d = an.describe([1.0, 2.0, 3.0])
    assert (d["n"], d["mean"], d["min"], d["p50"], d["max"]) == (3, 2.0, 1.0, 2.0, 3.0)
    assert d["std"] == pytest.approx(1.0)
    assert an.describe([7.0])["std"] == 0.0
    assert an.describe([])["n"] == 0


# --- experimento sintético ----------------------------------------------------

SPEC = """
name = "syn"
algorithm = "heartbeat"
seeds = 2
warmup_s = 0
duration_s = 10

[base]
N = 3
HB_INTERVAL_MS = 200

[sweep]
HB_ADAPTIVE = [false, true]

[[faults]]
at_s = 5
action = "kill"
target = "max"

[[plots]]
metric = "detection_latency_s"
x = "N"
series = "HB_ADAPTIVE"
series_labels = { false = "fixo", true = "adaptativo" }

[[plots]]
metric = "heartbeat_rate"
x = "N"
reference = "heartbeat_rate"
"""

START = 100.0


def ensaio(latency: float, false_at: list[float]) -> tuple[list[dict], list[dict], dict]:
    """3 nós; o nó 3 é morto em START+5 (comando de 0,3 s). Seu último evento
    é em START+5.1, então a latência deve contar a partir daí."""
    cfg = {"peers": {"1": [], "2": [], "3": []}, "hb_interval_ms": 200, "hb_timeout_ms": 600}
    records = [{"ts": 90.0, "node": n, "event": "node_start", "config": cfg} for n in (1, 2, 3)]
    # 10 heartbeats/s por nó (2 peers a cada 0,2 s) até o crash, na janela
    for k in range(50):
        for n in (1, 2, 3):
            records.append({"ts": START + k * 0.1, "node": n, "event": "msg_send", "type": "HEARTBEAT"})
    records.append({"ts": START + 5.1, "node": 3, "event": "msg_recv", "type": "HEARTBEAT"})
    for p in (1, 2):
        records.append({"ts": START + 5.1 + latency, "node": p, "event": "suspect", "peer": 3, "timeout_ms": 600})
    for t in false_at:
        records.append({"ts": t, "node": 1, "event": "suspect", "peer": 2, "timeout_ms": 600})
        records.append({"ts": t + 0.1, "node": 1, "event": "restore", "peer": 2,
                        "false_suspicion": True, "timeout_ms": 600})
    records.append({"ts": START + 10, "node": 1, "event": "msg_send", "type": "HEARTBEAT"})
    faults = [{"ts": START + 5.3, "event": "fault", "action": "kill", "target": 3,
               "issued_ts": START + 5.0, "cmd_ms": 300.0}]
    meta = {"complete": True, "n": 3, "measure_start_ts": START, "measure_end_ts": START + 10}
    return records, faults, meta


@pytest.fixture
def experiment(tmp_path):
    exp = tmp_path / "raw" / "syn"
    exp.mkdir(parents=True)
    (exp / "spec.toml").write_text(SPEC)
    cases = {
        ("false", 1): (0.5, [START + 2.0]),
        ("false", 2): (0.55, [START + 3.0, 95.0]),  # 95.0 cai no aquecimento: fora
        ("true", 1): (0.6, []),
        ("true", 2): (0.65, []),
    }
    for (adaptive, seed), (latency, false_at) in cases.items():
        run = exp / f"HB_ADAPTIVE={adaptive}" / f"seed-{seed:02d}"
        run.mkdir(parents=True)
        records, faults, meta = ensaio(latency, false_at)
        meta.update(config=f"HB_ADAPTIVE={adaptive}", seed=seed,
                    params={"N": 3, "HB_INTERVAL_MS": 200, "HB_ADAPTIVE": adaptive == "true"})
        text = "\n".join(json.dumps(r) for r in records) + "\n"
        if seed == 1:  # os dois formatos: comprimido (o do executor) e texto
            (run / "logs.jsonl.gz").write_bytes(gzip.compress(text.encode()))
        else:
            (run / "logs.jsonl").write_text(text)
        (run / "faults.jsonl").write_text("\n".join(json.dumps(r) for r in faults) + "\n")
        (run / "meta.json").write_text(json.dumps(meta))
    incomplete = exp / "HB_ADAPTIVE=true" / "seed-03"
    incomplete.mkdir()
    (incomplete / "meta.json").write_text(json.dumps({"complete": False}))
    return exp


def rows(path):
    with path.open() as f:
        return list(csv.DictReader(f))


def test_heartbeat_metrics_use_last_sign_of_life(experiment):
    run = an.load_runs(experiment)[0]
    scalars, samples = an.heartbeat_metrics(run["records"], run["meta"])
    # 0,5 s depois do último evento da vítima, não 0,6 s depois do disparo
    assert samples["detection_latency_s"] == pytest.approx([0.5, 0.5])
    assert scalars["heartbeat_rate"] == pytest.approx(30, rel=0.05)  # antes do crash
    assert scalars["false_suspicions"] == 1
    assert scalars["false_suspicion_rate"] == pytest.approx(1 / (6 * 10 / 60))
    assert scalars["violations"] == 0


def test_analyze_writes_csvs_and_plots(experiment, tmp_path, capsys):
    out = tmp_path / "results"
    assert an.main([str(experiment), "--out", str(out)]) == 0
    res = out / "syn"
    runs = rows(res / "runs.csv")
    assert len(runs) == 4  # o ensaio incompleto é ignorado
    samples = rows(res / "samples.csv")
    assert {s["metric"] for s in samples} == {"detection_latency_s"}  # sem recuperações
    summary = {(r["config"], r["metric"]): r for r in rows(res / "summary.csv")}
    fixed = summary[("HB_ADAPTIVE=false", "detection_latency_s")]
    assert int(fixed["n"]) == 4 and float(fixed["p50"]) == pytest.approx(0.525)
    assert float(summary[("HB_ADAPTIVE=false", "false_suspicions")]["mean"]) == 1.0
    assert int(summary[("HB_ADAPTIVE=true", "false_suspicions")]["n"]) == 2  # uma por seed
    for name in ("detection_latency_s__N__HB_ADAPTIVE", "heartbeat_rate__N"):
        for ext in ("png", "pdf"):
            assert (res / f"{name}.{ext}").stat().st_size > 1000
    assert "syn: 4 ensaios em 2 configurações" in capsys.readouterr().out


def test_no_complete_runs_is_an_error(tmp_path, capsys):
    exp = tmp_path / "syn"
    exp.mkdir()
    (exp / "spec.toml").write_text(SPEC)
    assert an.main([str(exp), "--out", str(tmp_path / "out")]) == 1
