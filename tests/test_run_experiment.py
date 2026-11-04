import json
from pathlib import Path

import pytest

from tests.scripts import ROOT, load_script

rx = load_script("run_experiment")

SPEC = """
name = "demo"
seeds = 2
warmup_s = 1
duration_s = 10

[base]
N = 4
HB_INTERVAL_MS = 200

[sweep]
DROP_RATE = [0.0, 0.1]
HB_ADAPTIVE = [false, true]

[[faults]]
at_s = 2
action = "kill"
target = "max"

[[faults]]
at_s = 5
action = "start"
target = 1
"""


def write(tmp_path: Path, text: str = SPEC) -> Path:
    path = tmp_path / "spec.toml"
    path.write_text(text)
    return path


def test_load_spec_and_grid(tmp_path):
    spec = rx.load_spec(write(tmp_path))
    assert (spec.name, spec.seeds, spec.warmup_s, spec.duration_s) == ("demo", 2, 1.0, 10.0)
    configs = spec.configs()
    assert len(configs) == 4
    assert configs[0] == {"N": 4, "HB_INTERVAL_MS": 200, "DROP_RATE": 0.0, "HB_ADAPTIVE": False}
    assert [f.node(4) for f in spec.faults] == [4, 1]


def test_plan_interleaves_configs_within_each_seed(tmp_path):
    spec = rx.load_spec(write(tmp_path))
    runs = rx.plan(spec, tmp_path / "raw")
    assert [r.seed for r in runs] == [1, 1, 1, 1, 2, 2, 2, 2]
    assert runs[1].dir == tmp_path / "raw" / "demo" / "DROP_RATE=0.0__HB_ADAPTIVE=true" / "seed-01"
    assert len(rx.plan(spec, tmp_path, seeds=1)) == 4


def test_run_env_excludes_n_and_sets_seed(tmp_path):
    run = rx.plan(rx.load_spec(write(tmp_path)), tmp_path)[1]
    assert run.n == 4
    assert run.env() == {"HB_INTERVAL_MS": "200", "DROP_RATE": "0.0", "HB_ADAPTIVE": "true", "SEED": "1"}


def test_completed_runs_are_detected(tmp_path):
    run = rx.plan(rx.load_spec(write(tmp_path)), tmp_path)[0]
    assert not run.is_complete()
    run.dir.mkdir(parents=True)
    (run.dir / "meta.json").write_text(json.dumps({"complete": False}))
    assert not run.is_complete()
    (run.dir / "meta.json").write_text(json.dumps({"complete": True}))
    assert run.is_complete()


def test_dry_run_lists_pending_runs(tmp_path, capsys):
    spec_path = write(tmp_path)
    done = rx.plan(rx.load_spec(spec_path), tmp_path / "raw")[0]
    done.dir.mkdir(parents=True)
    (done.dir / "meta.json").write_text(json.dumps({"complete": True}))
    assert rx.main([str(spec_path), "--out", str(tmp_path / "raw"), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "8 ensaios, 7 a executar" in out
    assert "DROP_RATE=0.0__HB_ADAPTIVE=false/seed-01" not in out
    assert "DROP_RATE=0.0__HB_ADAPTIVE=false/seed-02" in out


@pytest.mark.parametrize("patch", [
    ('name = "demo"', 'name = "demo exp"'),
    ("duration_s = 10", "duration_s = 0"),
    ("N = 4", 'N = "4"'),
    ('target = "max"', 'target = "leader"'),
    ("target = 1", "target = 9"),
    ('action = "kill"', 'action = "explode"'),
    ("at_s = 5", "at_s = 50"),
    ("DROP_RATE = [0.0, 0.1]", "DROP_RATE = []"),
    ("HB_INTERVAL_MS = 200", "HB_INTERVAL_MS = 200\nDROP_RATE = 0.1"),
])
def test_invalid_specs_are_rejected(tmp_path, patch):
    with pytest.raises(rx.SpecError):
        rx.load_spec(write(tmp_path, SPEC.replace(*patch)))


def test_invalid_spec_exit_code(tmp_path, capsys):
    assert rx.main([str(write(tmp_path, SPEC.replace("duration_s = 10", "")))]) == 2
    assert "inválida" in capsys.readouterr().err


@pytest.mark.parametrize("path", sorted((ROOT / "experiments" / "configs").glob("*.toml")), ids=lambda p: p.stem)
def test_committed_specs_are_valid(path):
    spec = rx.load_spec(path)
    assert spec.name == path.stem
    assert spec.raw.get("algorithm")
    for plot in spec.raw.get("plots", []):
        assert plot["x"] in {*spec.base, *spec.sweep}
        assert plot.get("series") in (None, *spec.sweep)
