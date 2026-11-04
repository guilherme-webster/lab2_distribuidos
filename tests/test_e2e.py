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

from tests.scripts import load_script

check_heartbeat = load_script("check_heartbeat")
ROOT = Path(__file__).resolve().parent.parent
N = 4
PROJECT = "mc714-e2e"
RUN_COMPOSE = ".run/e2e-compose.yaml"
ENV = {**os.environ, "COMPOSE_PROJECT_NAME": PROJECT}
STARTUP_TIMEOUT = 20.0
WARMUP = 3.0          # s de operação normal observados antes dos testes
EVENT_TIMEOUT = 15.0  # prazo para um evento esperado aparecer nos logs


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


def compose(*args: str) -> None:
    result = subprocess.run(
        ["docker", "compose", "--project-directory", ".", "-f", RUN_COMPOSE, *args],
        cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"docker compose {' '.join(args)} falhou:\n{result.stderr}"


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

    def after(self, name: str, ts: float, **match) -> list[dict]:
        return [
            r for r in self.events(name)
            if r["ts"] > ts and all(r.get(k) == v for k, v in match.items())
        ]

    def heard(self) -> set[tuple[int, int]]:
        """Pares (receptor, remetente) com ao menos uma mensagem recebida."""
        return {(r["node"], r["peer"]) for r in self.events("msg_recv")}


ALL_PAIRS = set(itertools.permutations(range(1, N + 1), 2))


def wait_logs(done, timeout: float = EVENT_TIMEOUT) -> Logs:
    """Relê os logs até `done(logs)` ser verdadeiro ou o prazo acabar."""
    deadline = time.monotonic() + timeout
    logs = Logs(make("logs"))
    while not done(logs) and time.monotonic() < deadline:
        time.sleep(0.5)
        logs = Logs(make("logs"))
    return logs


@pytest.fixture(scope="module")
def cluster():
    make("down")  # sobra de uma execução interrompida
    make("up", f"N={N}", timeout=300)
    try:
        wait_logs(lambda logs: logs.heard() == ALL_PAIRS, STARTUP_TIMEOUT)
        time.sleep(WARMUP)
        yield Logs(make("logs"))
    finally:
        make("down")
    assert project_containers() == [], "make down deixou contêineres para trás"
    assert not (ROOT / RUN_COMPOSE).exists(), "make down não removeu o compose gerado"


def hb_params(logs: Logs) -> tuple[float, float]:
    cfg = logs.events("node_start")[0]["config"]
    return cfg["hb_interval_ms"] / 1000, cfg["hb_timeout_ms"] / 1000


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


def test_no_suspicions_in_normal_operation(cluster):
    """H1 no sistema real: depois que todos sobem, ninguém é suspeito."""
    _, timeout = hb_params(cluster)
    # Contêineres sobem em instantes diferentes: um nó pode suspeitar de quem
    # ainda não subiu, e isso não é falsa suspeita
    all_up = max(r["ts"] for r in cluster.events("node_start"))
    assert cluster.after("suspect", all_up + timeout) == []
    for r in cluster.events("restore"):
        assert r["false_suspicion"] is False


def test_crash_is_detected_and_recovery_restored(cluster):
    """H2 + recuperação no sistema real. Roda por último: mata e religa um nó."""
    victim, survivors = N, range(1, N)
    _, timeout = hb_params(cluster)

    faults = []  # o docker kill não deixa rastro nos logs: registramos aqui
    killed_at = time.time()
    compose("kill", f"node{victim}")
    faults.append({"ts": killed_at, "event": "fault", "action": "kill", "target": victim})
    logs = wait_logs(lambda l: all(l.after("suspect", killed_at, node=i, peer=victim) for i in survivors))
    for i in survivors:
        suspects = logs.after("suspect", killed_at, node=i, peer=victim)
        assert suspects, f"nó {i} não suspeitou do nó {victim}"
        # Folga para o atraso do próprio docker kill e do escalonamento
        assert suspects[0]["ts"] - killed_at <= timeout + 0.5
    # Só a vítima é suspeita: a falha de um nó não atrapalha os demais
    wrong = [(r["node"], r["peer"]) for r in logs.after("suspect", killed_at) if r["peer"] != victim]
    assert wrong == [], f"falsas suspeitas após o crash (nó, suspeito): {wrong}"

    restarted_at = time.time()
    compose("start", f"node{victim}")
    faults.append({"ts": restarted_at, "event": "fault", "action": "start", "target": victim})
    logs = wait_logs(lambda l: all(l.after("restore", restarted_at, node=i, peer=victim) for i in survivors))
    for i in survivors:
        restores = logs.after("restore", restarted_at, node=i, peer=victim)
        assert restores, f"nó {i} não restaurou o nó {victim}"
        assert restores[0]["false_suspicion"] is False  # reinício, encarnação nova
    incarnations = [r["incarnation"] for r in logs.events("node_start") if r["node"] == victim]
    assert len(incarnations) == 2 and incarnations[1] > incarnations[0]
    assert [r for r in logs.after("suspect", restarted_at) if r["node"] == victim] == []

    # As mesmas propriedades, pelo verificador que os experimentos usam
    time.sleep(1.0)
    records = sorted(Logs(make("logs")).records + faults, key=lambda r: r["ts"])
    report = check_heartbeat.check(records, strict_accuracy=True)
    assert report.ok, check_heartbeat.format_report(report)
    assert len(report.detections) == len(survivors) and len(report.recoveries) == len(survivors)
