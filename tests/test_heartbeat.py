"""Detector de heartbeat no simulador (tempo virtual, determinístico)."""

import pytest

from dsnode.config import Config
from dsnode.heartbeat import FailureDetector
from dsnode.log import MemoryLog
from dsnode.messages import MessageFactory, MsgType
from tests.sim import FakeNetwork

INTERVAL, TIMEOUT = 0.2, 0.6
DELAY = 0.005


class Cluster:
    def __init__(self, n: int, start: list[int] | None = None, **net_kwargs):
        net_kwargs.setdefault("delay", DELAY)
        self.net = FakeNetwork(list(range(1, n + 1)), **net_kwargs)
        self.clock = self.net.clock
        self.det: dict[int, FailureDetector] = {}
        self.logs: dict[int, MemoryLog] = {}
        self._starting = start if start is not None else list(range(1, n + 1))

    async def start(self) -> "Cluster":
        for i in self._starting:
            await self.boot(i)
        return self

    async def boot(self, i: int, incarnation: int = 0) -> FailureDetector:
        log = MemoryLog(i, self.clock.now)
        cfg = Config(node_id=i, hb_interval_ms=INTERVAL * 1000, hb_timeout_ms=TIMEOUT * 1000)
        det = FailureDetector(self.net.transport(i, log), self.clock, cfg, log, MessageFactory(i, incarnation))
        await det.start()
        self.det[i], self.logs[i] = det, log
        return det

    def crash(self, i: int) -> None:
        # Para também as tarefas da instância, para ela não virar zumbi
        self.net.crash(i)
        self.det[i].stop()

    def events(self, i: int, name: str) -> list[dict]:
        return self.logs[i].events(name)

    def alive(self) -> list[int]:
        return [i for i in self.det if self.net.is_up(i)]


async def cluster(n: int, **kwargs) -> Cluster:
    return await Cluster(n, **kwargs).start()


# --- H1: precisão sem falhas -----------------------------------------------

@pytest.mark.parametrize("jitter", [0.0, 0.05])
async def test_no_failures_no_suspicions(jitter):
    c = await cluster(5, jitter=jitter, seed=1)
    await c.net.run(30.0)
    for i in c.alive():
        assert c.events(i, "suspect") == []
        assert c.det[i].alive_peers() == set(c.det) - {i}


# --- H2: completude após crash ---------------------------------------------

async def test_crash_is_detected_by_all_within_bound():
    c = await cluster(5)
    await c.net.run(2.0)
    crash_at = c.clock.now()
    c.crash(3)
    await c.net.run(5.0)

    for i in c.alive():
        suspects = c.events(i, "suspect")
        assert [s["peer"] for s in suspects] == [3], i
        # Último heartbeat do nó 3 saiu em (crash - intervalo, crash] e chegou
        # DELAY depois; a suspeita vem TIMEOUT após essa chegada
        detected = suspects[0]["ts"] - crash_at
        assert TIMEOUT - INTERVAL + DELAY <= detected <= TIMEOUT + DELAY + 1e-9
        assert suspects[0]["last_seen_ms"] == pytest.approx(TIMEOUT * 1000)
        assert c.det[i].is_suspected(3)
        assert c.events(i, "restore") == []  # continua suspeito


async def test_node_that_never_starts_is_suspected_after_timeout():
    c = await cluster(3, start=[1, 2])
    await c.net.run(2.0)
    for i in (1, 2):
        suspects = c.events(i, "suspect")
        assert [s["peer"] for s in suspects] == [3]
        assert suspects[0]["ts"] == pytest.approx(TIMEOUT)


async def test_stop_halts_sending_and_timers():
    c = await cluster(3)
    await c.net.run(1.0)
    c.det[1].stop()  # nó 1 continua na rede, mas para de enviar e de vigiar
    sent_before = len(c.net.sent)
    await c.net.run(5.0)
    assert c.events(1, "suspect") == []
    assert all(m.src != 1 for m in c.net.sent[sent_before:])
    for i in (2, 3):
        assert [s["peer"] for s in c.events(i, "suspect")] == [1]


# --- H3: suspeita seguida de restore ---------------------------------------

async def test_partition_causes_suspicion_then_restore_on_heal():
    c = await cluster(3)
    await c.net.run(1.0)
    c.net.partition({1, 2}, {3})
    await c.net.run(2.0)
    assert {s["peer"] for s in c.events(1, "suspect")} == {3}
    assert {s["peer"] for s in c.events(3, "suspect")} == {1, 2}

    healed_at = c.clock.now()
    c.net.heal()
    await c.net.run(1.0)
    for i, peers in ((1, {3}), (2, {3}), (3, {1, 2})):
        restores = c.events(i, "restore")
        assert {r["peer"] for r in restores} == peers
        assert all(r["ts"] - healed_at <= INTERVAL + DELAY + 1e-9 for r in restores)
        assert c.det[i].alive_peers() == {1, 2, 3} - {i}


async def test_recovered_node_is_restored():
    c = await cluster(3)
    await c.net.run(1.0)
    c.crash(3)
    await c.net.run(2.0)
    await c.boot(3, incarnation=1)  # reinício: estado vazio, nova encarnação
    await c.net.run(1.0)
    for i in (1, 2):
        assert [r["peer"] for r in c.events(i, "restore")] == [3]
        assert c.events(i, "restore")[0]["suspected_ms"] > 0
    assert c.events(3, "suspect") == []  # o recém-chegado vê todos vivos


async def test_loss_causes_only_transient_false_suspicions():
    c = await cluster(4, drop_rate=0.4, seed=7)
    await c.net.run(30.0)
    false_suspicions = sum(len(c.events(i, "suspect")) for i in c.alive())
    assert false_suspicions > 0  # perdas violam a precisão...
    c.net.drop_rate = 0.0
    await c.net.run(1.0)
    for i in c.alive():  # ...mas toda suspeita é desfeita
        assert len(c.events(i, "restore")) == len(c.events(i, "suspect"))
        assert c.det[i].alive_peers() == set(c.det) - {i}


# --- heartbeats velhos, duplicados e malformados ---------------------------

async def test_stale_duplicate_and_malformed_heartbeats_are_ignored():
    net = FakeNetwork([1, 2], delay=DELAY)
    log = MemoryLog(1, net.clock.now)
    cfg = Config(node_id=1, hb_interval_ms=INTERVAL * 1000, hb_timeout_ms=TIMEOUT * 1000)
    det = FailureDetector(net.transport(1, log), net.clock, cfg, log, MessageFactory(1))
    await det.start()
    raw = net.transport(2)  # nó 2 "manual": sem detector, mandamos o que quisermos
    f2 = MessageFactory(2)

    async def hb(**payload):
        await raw.send(1, f2.new(MsgType.HEARTBEAT, 1, payload))
        await net.run(0.01)

    await hb(inc=0, seq=5)
    await net.run(1.0)
    assert det.is_suspected(2)

    await hb(inc=0, seq=3)   # atrasado
    await hb(inc=0, seq=5)   # duplicado
    await hb(seq=9)          # sem encarnação
    await hb(inc=0, seq="x")  # malformado
    assert det.is_suspected(2) and log.events("restore") == []

    await hb(inc=0, seq=6)
    assert not det.is_suspected(2)
    await net.run(1.0)
    await hb(inc=1, seq=1)   # reinício: seq volta a 1, mas a encarnação cresceu
    assert [r["peer"] for r in log.events("restore")] == [2, 2]


# --- inscritos (integração com o Bully) ------------------------------------

async def test_subscribers_are_notified_and_errors_isolated():
    c = await cluster(3)
    calls = []

    def broken(peer):
        raise RuntimeError("bug no inscrito")

    c.det[1].subscribe(broken, broken)
    c.det[1].subscribe(lambda p: calls.append(("suspect", p)), lambda p: calls.append(("restore", p)))
    await c.net.run(1.0)
    c.net.partition({1, 2}, {3})
    await c.net.run(1.0)
    c.net.heal()
    await c.net.run(1.0)

    assert calls == [("suspect", 3), ("restore", 3)]
    assert len(c.events(1, "handler_error")) == 2


# --- custo em mensagens -----------------------------------------------------

@pytest.mark.parametrize("n", [3, 5, 9])
async def test_message_cost_is_quadratic(n):
    c = await cluster(n)
    await c.net.run(10.0)
    heartbeats = [m for m in c.net.sent if m.type == MsgType.HEARTBEAT]
    rounds = len(heartbeats) / (n * (n - 1))
    assert rounds == pytest.approx(10.0 / INTERVAL, abs=1)
