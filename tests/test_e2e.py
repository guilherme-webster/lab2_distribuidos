"""Teste de ponta a ponta: sobe o ambiente real pelo Makefile e verifica os logs.

Roda num projeto Docker próprio (mc714-e2e) e num compose próprio em .run/,
para não interferir num `make up` em andamento. Usa N=4 para exercitar o
caminho de N diferente do compose.yaml versionado.
"""

import itertools
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
N = 4
PROJECT = "mc714-e2e"
RUN_COMPOSE = ".run/e2e-compose.yaml"
ENV = {**os.environ, "COMPOSE_PROJECT_NAME": PROJECT}
STARTUP_TIMEOUT = 20.0


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except subprocess.TimeoutExpired:
        return False


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not docker_available(), reason="Docker indisponível"),
]


def make(*args: str, timeout: float = 60) -> str:
    result = subprocess.run(
        ["make", "-s", *args, f"RUN_COMPOSE={RUN_COMPOSE}"],
        cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=timeout,
    )
    assert result.returncode == 0, f"make {' '.join(args)} falhou:\n{result.stderr}"
    return result.stdout


def project_containers() -> list[str]:
    out = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={PROJECT}"],
        capture_output=True, text=True, timeout=10,
    ).stdout
    return out.split()


class Logs:
    def __init__(self, text: str):
        self.records: list[dict] = []
        self.garbage: list[str] = []  # linhas fora do formato JSON (ex.: traceback)
        for line in text.splitlines():
            try:
                self.records.append(json.loads(line))
            except json.JSONDecodeError:
                self.garbage.append(line)

    def events(self, name: str) -> list[dict]:
        return [r for r in self.records if r["event"] == name]

    def heard(self) -> set[tuple[int, int]]:
        """Pares (receptor, remetente) com ao menos uma mensagem recebida."""
        return {(r["node"], r["peer"]) for r in self.events("msg_recv")}


ALL_PAIRS = set(itertools.permutations(range(1, N + 1), 2))


@pytest.fixture(scope="module")
def cluster():
    make("down")  # sobra de uma execução interrompida
    make("up", f"N={N}", timeout=300)
    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT
        logs = Logs(make("logs"))
        while logs.heard() != ALL_PAIRS and time.monotonic() < deadline:
            time.sleep(0.5)
            logs = Logs(make("logs"))
        yield logs
    finally:
        make("down")
    assert project_containers() == [], "make down deixou contêineres para trás"
    assert not (ROOT / RUN_COMPOSE).exists(), "make down não removeu o compose gerado"


def test_every_node_started_once(cluster):
    started = sorted(r["node"] for r in cluster.events("node_start"))
    assert started == list(range(1, N + 1))


def test_every_pair_communicates(cluster):
    missing = ALL_PAIRS - cluster.heard()
    assert not missing, f"pares (receptor, remetente) sem mensagens: {sorted(missing)}"


def test_config_reaches_nodes(cluster):
    for r in cluster.events("node_start"):
        assert sorted(int(p) for p in r["config"]["peers"]) == [p for p in range(1, N + 1) if p != r["node"]]


def test_logs_are_clean(cluster):
    assert cluster.garbage == []
    for bad in ("handler_error", "msg_invalid", "node_stop"):
        assert cluster.events(bad) == [], bad
