"""Eleição Bully com tempo virtual e falhas determinísticas."""

import pytest

from dsnode.bully import Bully
from dsnode.config import Config
from dsnode.heartbeat import FailureDetector
from dsnode.log import MemoryLog
from dsnode.messages import MessageFactory, MsgType
from tests.sim import FakeNetwork


class Cluster:
    def __init__(self, n, **kwargs):
        self.net = FakeNetwork(list(range(1, n + 1)), seed=7, delay=0.005, **kwargs)
        self.nodes = {}
        self.detectors = {}
        self.logs = {}

    async def boot(self, node_id, incarnation=0):
        cfg = Config(node_id=node_id)
        log = MemoryLog(node_id, self.net.clock.now)
        transport = self.net.transport(node_id, log)
        factory = MessageFactory(node_id, incarnation)
        detector = FailureDetector(transport, self.net.clock, cfg, log, factory)
        node = Bully(transport, self.net.clock, cfg, log, detector)
        self.nodes[node_id] = node
        self.detectors[node_id] = detector
        self.logs[node_id] = log
        await detector.start()
        await node.start()
        return node

    def crash(self, node_id):
        self.nodes[node_id].stop()
        self.detectors[node_id].stop()
        self.net.crash(node_id)

    def assert_leader(self, leader):
        for node_id, node in self.nodes.items():
            if self.net.is_up(node_id):
                assert node.leader() == leader, node_id
            assert self.logs[node_id].events("handler_error") == []

    def close(self):
        for node in self.nodes.values():
            node.stop()
        for detector in self.detectors.values():
            detector.stop()


@pytest.fixture
def cluster():
    clusters = []

    async def create(n, start=None, **kwargs):
        c = Cluster(n, **kwargs)
        clusters.append(c)
        for node_id in range(1, n + 1) if start is None else start:
            await c.boot(node_id)
        return c

    yield create
    for c in clusters:
        c.close()


@pytest.mark.parametrize("n", [1, 3, 5, 9])
async def test_startup_elects_highest_id_and_stays_stable(cluster, n):
    c = await cluster(n, jitter=0.02)
    await c.net.run(3)
    c.assert_leader(n)
    starts = [len(log.events("election_start")) for log in c.logs.values()]
    await c.net.run(5)
    c.assert_leader(n)
    assert starts == [len(log.events("election_start")) for log in c.logs.values()]
    messages = c.net.sent
    assert len({msg.msg_id for msg in messages}) == len(messages)


async def test_highest_node_never_starts(cluster):
    c = await cluster(5, start=[1, 2, 3, 4])
    await c.net.run(3)
    c.assert_leader(4)
    for log in c.logs.values():
        assert log.events("leader_change")[-1]["leader"] == 4


async def test_heartbeat_suspicion_triggers_election_after_leader_crash(cluster):
    c = await cluster(5)
    await c.net.run(1)
    c.assert_leader(5)
    c.crash(5)
    await c.net.run(3)
    c.assert_leader(4)
    for node_id in range(1, 5):
        log = c.logs[node_id]
        assert any(e["peer"] == 5 for e in log.events("suspect"))
        assert any(e["reason"] == "leader_suspected" for e in log.events("election_start"))


async def test_highest_node_recovers_and_reclaims_leadership(cluster):
    c = await cluster(5)
    await c.net.run(1)
    c.crash(5)
    await c.net.run(3)
    c.assert_leader(4)
    previous = c.logs[5].events("election_start")[0]["election_id"]
    await c.boot(5, incarnation=1)
    await c.net.run(3)
    c.assert_leader(5)
    assert c.logs[5].events("election_start")[0]["election_id"] != previous


async def test_candidate_crashes_after_answer_before_coordinator(cluster):
    c = await cluster(5, start=[1, 2, 3, 4])
    await c.net.run(0.05)
    assert c.logs[1].events("answer_recv")
    assert c.logs[4].events("coordinator_sent") == []
    c.crash(4)
    await c.net.run(4)
    c.assert_leader(3)
    assert any(
        event["reason"] == "coordinator_timeout"
        for event in c.logs[1].events("election_start")
    )


async def test_duplicate_requests_do_not_create_parallel_attempts(cluster):
    c = await cluster(3, start=[1])
    node = c.nodes[1]
    await node.start()
    for _ in range(5):
        await node.start_election("manual")
    assert len(c.logs[1].events("election_start")) == 1
    await c.net.run(0.51)
    assert node.leader() == 1


async def test_stale_and_duplicate_answers_do_not_extend_timeout(cluster):
    c = await cluster(3, start=[1])
    raw = c.net.transport(3)
    factory = MessageFactory(3)
    election_id = c.logs[1].events("election_start")[0]["election_id"]

    async def answer(attempt):
        await raw.send(1, factory.new(MsgType.ANSWER, 1, {"election_id": attempt}))
        await c.net.run(0.01)

    await answer(election_id)
    await c.net.run(0.7)
    await answer(election_id)
    await c.net.run(0.3)
    assert len(c.logs[1].events("election_start")) == 2
    await answer(election_id)
    await c.net.run(0.6)
    assert c.nodes[1].leader() == 1
    assert len(c.logs[1].events("answer_recv")) == 1


async def test_lost_coordinator_is_reannounced_on_retry(cluster):
    c = await cluster(3, start=[1, 2])
    # O maior inicia isolado; só seu primeiro anúncio será perdido.
    c.net.partition({1, 2}, {3})
    await c.boot(3)
    await c.net.run(0.1)
    c.net.heal()
    await c.net.run(3)
    c.assert_leader(3)
    assert len(c.logs[3].events("coordinator_sent")) > 2


async def test_lower_coordinator_is_contested(cluster):
    c = await cluster(3, start=[3])
    raw = c.net.transport(1)
    await raw.send(3, MessageFactory(1).new(MsgType.COORDINATOR, 3, {"election_id": "1-0-1"}))
    await c.net.run(0.01)
    assert c.nodes[3].leader() == 3
    assert len(c.logs[3].events("coordinator_sent")) == 4


async def test_delayed_lower_coordinator_does_not_replace_known_leader(cluster):
    c = await cluster(3)
    await c.net.run(1)
    c.assert_leader(3)
    await c.nodes[2].transport.send(
        1, c.nodes[2].factory.new(MsgType.COORDINATOR, 1, {"election_id": "2-0-old"})
    )
    await c.net.run(0.01)
    c.assert_leader(3)


async def test_delayed_coordinator_from_suspected_leader_triggers_new_attempt(cluster):
    c = await cluster(3)
    await c.net.run(1)
    c.crash(3)
    await c.net.run(2)
    c.assert_leader(2)
    raw = c.net.transport(3)
    await raw.send(1, MessageFactory(3, 1).new(MsgType.COORDINATOR, 1, {"election_id": "3-0-old"}))
    await c.net.run(0.02)
    assert any(
        event["reason"] == "coordinator_suspected"
        for event in c.logs[1].events("election_start")
    )
    assert c.nodes[1].leader() == 2


async def test_malformed_messages_and_wrong_destinations_are_ignored(cluster):
    c = await cluster(3, start=[1])
    raw = c.net.transport(3)
    factory = MessageFactory(3)
    for payload in ({}, {"election_id": 1}, {"election_id": ""}):
        for msg_type in (MsgType.ELECTION, MsgType.ANSWER, MsgType.COORDINATOR):
            await raw.send(1, factory.new(msg_type, 1, payload))
    await raw.send(1, factory.new(MsgType.COORDINATOR, 2, {"election_id": "3-0-1"}))
    await c.net.run(0.51)
    assert c.nodes[1].leader() == 1
    assert c.logs[1].events("answer_recv") == []


async def test_stop_cancels_pending_election_and_ignores_messages(cluster):
    c = await cluster(3, start=[1])
    node = c.nodes[1]
    node.stop()
    before = len(c.net.sent)
    raw = c.net.transport(2)
    await raw.send(1, MessageFactory(2).new(MsgType.ELECTION, 1, {"election_id": "2-0-1"}))
    await c.net.run(3)
    assert node.leader() is None
    assert not [m for m in c.net.sent[before:] if m.src == 1 and m.type != MsgType.HEARTBEAT]
    assert c.logs[1].events("leader_change") == []
