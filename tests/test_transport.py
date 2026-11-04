"""Testes do transporte UDP real em 127.0.0.1 (usam tempo real, mas curto)."""

import asyncio
import socket

import pytest

from dsnode.config import Config
from dsnode.log import MemoryLog
from dsnode.messages import MessageFactory
from dsnode import transport as transport_mod
from dsnode.transport import UdpTransport

WAIT = 1.0


def free_ports(n: int) -> list[int]:
    socks = [socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    return ports


async def make_pair(**sender_cfg):
    """Nós 1 e 2 em localhost; sender_cfg configura falhas no nó 1."""
    p1, p2 = free_ports(2)
    addrs = {1: ("127.0.0.1", p1), 2: ("localhost", p2)}
    nodes = []
    for node_id in (1, 2):
        peers = {k: v for k, v in addrs.items() if k != node_id}
        extra = sender_cfg if node_id == 1 else {}
        cfg = Config(node_id=node_id, peers=peers, host="127.0.0.1", port=addrs[node_id][1], **extra)
        t = UdpTransport(cfg, MemoryLog(node_id))
        await t.start()
        nodes.append(t)
    return nodes


@pytest.fixture
async def pair():
    nodes = await make_pair()
    yield nodes
    for t in nodes:
        t.close()


def inbox_for(transport, msg_type="PING"):
    inbox: asyncio.Queue = asyncio.Queue()

    async def handler(msg):
        await inbox.put(msg)

    transport.on(msg_type, handler)
    return inbox


async def test_exchange_both_directions(pair):
    t1, t2 = pair
    in1, in2 = inbox_for(t1, "PONG"), inbox_for(t2)

    sent = MessageFactory(1).new("PING", 2, {"n": 1})
    await t1.send(2, sent)
    got = await asyncio.wait_for(in2.get(), WAIT)
    assert got == sent

    await t2.send(1, MessageFactory(2).new("PONG", 1))
    assert (await asyncio.wait_for(in1.get(), WAIT)).src == 2

    assert t1.log.events("msg_send")[0]["msg_id"] == sent.msg_id
    assert t2.log.events("msg_recv")[0]["msg_id"] == sent.msg_id


async def test_unhandled_type_is_received_but_ignored(pair):
    t1, t2 = pair
    in2 = inbox_for(t2)
    await t1.send(2, MessageFactory(1).new("OTHER", 2))
    await t1.send(2, MessageFactory(1).new("PING", 2))
    assert (await asyncio.wait_for(in2.get(), WAIT)).type == "PING"
    assert [r["type"] for r in t2.log.events("msg_recv")] == ["OTHER", "PING"]


async def test_invalid_datagrams_are_logged_and_dropped(pair):
    t1, t2 = pair
    in2 = inbox_for(t2)
    raw = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = ("127.0.0.1", t2.cfg.port)
    raw.sendto(b"lixo", addr)
    # dst errado e src desconhecido
    raw.sendto(MessageFactory(1).new("PING", 3).to_bytes(), addr)
    raw.sendto(MessageFactory(9).new("PING", 2).to_bytes(), addr)
    raw.close()
    await t1.send(2, MessageFactory(1).new("PING", 2))
    await asyncio.wait_for(in2.get(), WAIT)
    assert len(t2.log.events("msg_invalid")) == 3
    assert in2.empty()


async def test_handler_error_is_logged_and_node_keeps_working(pair):
    t1, t2 = pair
    calls = []

    async def flaky(msg):
        calls.append(msg)
        if len(calls) == 1:
            raise RuntimeError("bug")

    t2.on("PING", flaky)
    f = MessageFactory(1)
    await t1.send(2, f.new("PING", 2))
    await t1.send(2, f.new("PING", 2))
    for _ in range(100):
        if len(calls) == 2:
            break
        await asyncio.sleep(0.01)
    assert len(calls) == 2
    assert "bug" in t2.log.events("handler_error")[0]["error"]


async def test_unknown_peer_raises(pair):
    t1, _ = pair
    with pytest.raises(ValueError):
        await t1.send(7, MessageFactory(1).new("PING", 7))


async def wait_events(log, name: str, count: int, timeout: float = WAIT) -> list[dict]:
    deadline = asyncio.get_running_loop().time() + timeout
    while len(log.events(name)) < count and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    return log.events(name)


async def unresolvable_transport() -> UdpTransport:
    (port,) = free_ports(1)
    cfg = Config(node_id=1, peers={2: ("no-such-node.invalid", 9000)}, host="127.0.0.1", port=port)
    t = UdpTransport(cfg, MemoryLog(1))
    await t.start()
    return t


async def test_unresolvable_peer_is_dropped_not_raised():
    t = await unresolvable_transport()
    try:
        await t.send(2, MessageFactory(1).new("PING", 2))
        drops = await wait_events(t.log, "msg_drop", 1)
    finally:
        t.close()
    assert len(t.log.events("msg_send")) == 1
    assert [r["reason"] for r in drops] == ["resolve"]


async def test_failed_resolution_is_cached_briefly():
    t = await unresolvable_transport()
    lookups = []
    original = t._lookup

    async def counting(dst):
        lookups.append(dst)
        return await original(dst)

    t._lookup = counting
    f = MessageFactory(1)
    try:
        # Envios simultâneos compartilham uma consulta
        for _ in range(5):
            await t.send(2, f.new("PING", 2))
        await wait_events(t.log, "msg_drop", 5)
        assert lookups == [2]
        # Logo depois da falha: descarte imediato, sem nova consulta
        await t.send(2, f.new("PING", 2))
        assert len(t.log.events("msg_drop")) == 6 and lookups == [2]
        # Passada a validade, tenta resolver de novo (o nó pode ter voltado)
        await asyncio.sleep(transport_mod.RESOLVE_FAIL_TTL + 0.05)
        await t.send(2, f.new("PING", 2))
        await wait_events(t.log, "msg_drop", 7)
        assert lookups == [2, 2]
    finally:
        t.close()


async def test_slow_dns_for_one_peer_does_not_delay_others():
    """Regressão: o nome de um contêiner morto pode levar RESOLVE_TIMEOUT para
    falhar; o send() esperava, e o laço de heartbeats atrasava para todos."""
    t1, t2 = await make_pair()
    try:
        inbox = inbox_for(t2)
        loop = asyncio.get_running_loop()
        original = t1._lookup

        async def slow_dns(dst):
            await asyncio.sleep(0.3)
            return await original(dst)

        t1._lookup = slow_dns
        t1._cache.clear()
        start = loop.time()
        await t1.send(2, MessageFactory(1).new("PING", 2))
        assert loop.time() - start < 0.05  # send() não espera pelo DNS
        await asyncio.wait_for(inbox.get(), WAIT)  # e a mensagem chega depois
    finally:
        t1.close()
        t2.close()


# --- injeção de falhas -----------------------------------------------------

async def drain(inbox: asyncio.Queue, quiet: float = 0.1) -> list:
    """Coleta mensagens até a fila ficar quieta por `quiet` segundos."""
    got = []
    while True:
        try:
            got.append(await asyncio.wait_for(inbox.get(), quiet))
        except TimeoutError:
            return got


async def send_burst(drop_rate: float, seed: int, n: int = 100):
    t1, t2 = await make_pair(drop_rate=drop_rate, seed=seed)
    try:
        inbox = inbox_for(t2)
        f = MessageFactory(1)
        for _ in range(n):
            await t1.send(2, f.new("PING", 2))
        received = {m.msg_id for m in await drain(inbox)}
        dropped = {r["msg_id"] for r in t1.log.events("msg_drop")}
        assert len(t1.log.events("msg_send")) == n
        assert all(r["reason"] == "loss" for r in t1.log.events("msg_drop"))
        return received, dropped
    finally:
        t1.close()
        t2.close()


async def test_drop_rate_extremes():
    received, dropped = await send_burst(drop_rate=1.0, seed=0, n=20)
    assert received == set() and len(dropped) == 20
    received, dropped = await send_burst(drop_rate=0.0, seed=0, n=20)
    assert len(received) == 20 and dropped == set()


async def test_loss_is_reproducible_with_same_seed():
    recv_a, drop_a = await send_burst(drop_rate=0.3, seed=42)
    recv_b, drop_b = await send_burst(drop_rate=0.3, seed=42)
    _, drop_c = await send_burst(drop_rate=0.3, seed=43)
    assert drop_a == drop_b and drop_a != drop_c
    assert recv_a == recv_b  # em localhost não há perda real
    assert 15 < len(drop_a) < 45


async def test_delay_and_jitter_postpone_delivery():
    t1, t2 = await make_pair(delay_ms=100, jitter_ms=50)
    try:
        inbox = inbox_for(t2)
        loop = asyncio.get_running_loop()
        start = loop.time()
        await t1.send(2, MessageFactory(1).new("PING", 2))
        assert len(t1.log.events("msg_send")) == 1  # registrado na hora do envio
        await asyncio.wait_for(inbox.get(), WAIT)
        assert 0.1 <= loop.time() - start < 0.4
    finally:
        t1.close()
        t2.close()


async def test_close_cancels_delayed_sends():
    t1, t2 = await make_pair(delay_ms=200)
    try:
        inbox = inbox_for(t2)
        await t1.send(2, MessageFactory(1).new("PING", 2))
        t1.close()
        assert await drain(inbox, quiet=0.3) == []
    finally:
        t2.close()


async def test_block_and_unblock(pair):
    t1, t2 = pair
    inbox = inbox_for(t2)
    f = MessageFactory(1)

    t1.block(2)
    await t1.send(2, f.new("PING", 2))
    assert [r["reason"] for r in t1.log.events("msg_drop")] == ["partition"]

    t1.unblock()
    await t1.send(2, f.new("PING", 2))
    assert len(await drain(inbox)) == 1
    assert [r["peers"] for r in t1.log.events("block")] == [[2], []]
