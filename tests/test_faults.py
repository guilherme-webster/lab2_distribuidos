"""scripts/faults.sh com um `docker` falso no PATH (sem Docker de verdade)."""

import json
import os
import subprocess

import pytest

from tests.scripts import ROOT, load_script

SCRIPT = ROOT / "scripts" / "faults.sh"
check_heartbeat = load_script("check_heartbeat")


@pytest.fixture
def fake(tmp_path):
    """Ambiente com docker falso: registra os argumentos e sai com $DOCKER_EXIT."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.txt"
    docker = bin_dir / "docker"
    docker.write_text(f'#!/usr/bin/env bash\necho "$*" >> {calls}\nexit "${{DOCKER_EXIT:-0}}"\n')
    docker.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAULTS_FILE": str(tmp_path / "faults.jsonl"),
        "RUN_COMPOSE": str(tmp_path / "nao-existe.yaml"),
    }

    class Fake:
        def run(self, *args, **extra_env):
            return subprocess.run(
                [str(SCRIPT), *args], env={**env, **extra_env},
                capture_output=True, text=True, timeout=30,
            )

        def calls(self):
            return calls.read_text().splitlines() if calls.exists() else []

        def records(self):
            path = tmp_path / "faults.jsonl"
            return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    fake_env = Fake()
    fake_env.tmp_path = tmp_path
    return fake_env


def test_kill_runs_compose_and_records_each_node(fake):
    result = fake.run("kill", "3", "4")
    assert result.returncode == 0, result.stderr
    assert fake.calls() == [
        "compose --project-directory . -f compose.yaml kill node3",
        "compose --project-directory . -f compose.yaml kill node4",
    ]
    records = fake.records()
    assert [(r["event"], r["action"], r["target"]) for r in records] == [("fault", "kill", 3), ("fault", "kill", 4)]
    for r in records:
        assert r["issued_ts"] <= r["ts"] and r["cmd_ms"] >= 0
    # O mesmo registro sai no stdout
    assert [json.loads(line) for line in result.stdout.splitlines()] == records


@pytest.mark.parametrize("action", ["start", "pause", "unpause"])
def test_other_actions(fake, action):
    assert fake.run(action, "2").returncode == 0
    assert fake.calls() == [f"compose --project-directory . -f compose.yaml {action} node2"]
    assert fake.records()[0]["action"] == action


def test_stop_uses_short_grace_period(fake):
    assert fake.run("stop", "1").returncode == 0
    assert fake.calls() == ["compose --project-directory . -f compose.yaml stop -t 2 node1"]


def test_uses_generated_compose_when_present(fake):
    generated = fake.tmp_path / "gerado.yaml"
    generated.write_text("services: {}\n")
    assert fake.run("kill", "1", RUN_COMPOSE=str(generated)).returncode == 0
    assert fake.calls() == [f"compose --project-directory . -f {generated} kill node1"]


def test_records_are_appended(fake):
    fake.run("kill", "1")
    fake.run("start", "1")
    assert [r["action"] for r in fake.records()] == ["kill", "start"]


@pytest.mark.parametrize("args", [(), ("kill",), ("explode", "1"), ("kill", "node1"), ("kill", "1", "x")])
def test_invalid_usage_does_nothing(fake, args):
    result = fake.run(*args)
    assert result.returncode == 2
    assert fake.calls() == [] and fake.records() == []


def test_docker_failure_is_not_recorded(fake):
    result = fake.run("kill", "1", DOCKER_EXIT="1")
    assert result.returncode == 1
    assert "falhou" in result.stderr
    assert fake.records() == []


def test_records_are_readable_by_the_checker(fake):
    fake.run("pause", "2")
    fake.run("unpause", "2")
    records, garbage = check_heartbeat.load([fake.tmp_path / "faults.jsonl"])
    assert garbage == 0 and [r["action"] for r in records] == ["pause", "unpause"]
