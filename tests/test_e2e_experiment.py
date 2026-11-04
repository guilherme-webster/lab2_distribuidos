"""Ponta a ponta do pipeline de experimentos: executar no Docker e analisar."""

import json

import pytest

from tests.scripts import load_script
from tests.test_e2e import pytestmark  # noqa: F401  (marcador docker + skip sem Docker)

SPEC = """
name = "e2e-tiny"
algorithm = "heartbeat"
seeds = 1
warmup_s = 1
duration_s = 5

[base]
N = 3

[sweep]
HB_TIMEOUT_MS = [600]

[[faults]]
at_s = 1.5
action = "kill"
target = "max"

[[plots]]
metric = "detection_latency_s"
x = "HB_TIMEOUT_MS"
"""


def test_run_and_analyze_tiny_experiment(tmp_path, monkeypatch, capsys):
    # Projeto próprio: não colide com um experimento de verdade em andamento
    monkeypatch.setenv("EXP_PROJECT", "mc714-e2e-exp")
    monkeypatch.setenv("EXP_COMPOSE_FILE", ".run/e2e-exp-compose.yaml")
    rx = load_script("run_experiment")
    an = load_script("analyze")
    spec = tmp_path / "e2e-tiny.toml"
    spec.write_text(SPEC)
    raw, results = tmp_path / "raw", tmp_path / "results"

    assert rx.main([str(spec), "--out", str(raw)]) == 0
    run_dir = raw / "e2e-tiny" / "HB_TIMEOUT_MS=600" / "seed-01"
    meta = json.loads((run_dir / "meta.json").read_text())
    assert meta["complete"] and meta["n"] == 3 and meta["seed"] == 1
    assert meta["faults_planned"] == [{"at_s": 1.5, "action": "kill", "target": 3}]
    assert meta["measure_end_ts"] - meta["measure_start_ts"] == pytest.approx(5, abs=0.5)
    assert (raw / "e2e-tiny" / "env.json").exists() and (raw / "e2e-tiny" / "spec.toml").exists()
    assert (run_dir / "logs.jsonl.gz").exists() and not (run_dir / "logs.jsonl").exists()

    # Retomada: o ensaio completo não é refeito
    capsys.readouterr()
    assert rx.main([str(spec), "--out", str(raw)]) == 0
    assert "0 a executar" in capsys.readouterr().out

    assert an.main([str(raw / "e2e-tiny"), "--out", str(results)]) == 0
    scalars, samples = an.heartbeat_metrics(an.load_runs(raw / "e2e-tiny")[0]["records"], meta)
    assert scalars["violations"] == 0 and scalars["false_suspicions"] == 0
    assert len(samples["detection_latency_s"]) == 2  # 2 observadores do nó 3
    assert all(0.4 - 0.1 < v <= 0.6 + 0.1 for v in samples["detection_latency_s"])
    assert (results / "e2e-tiny" / "detection_latency_s__HB_TIMEOUT_MS.png").exists()
