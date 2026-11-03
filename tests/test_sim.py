import asyncio

import pytest

from dsnode.log import MemoryLog
from dsnode.messages import MessageFactory
from tests.sim import FakeClock, FakeNetwork


# --- FakeClock -------------------------------------------------------------

async def test_timers_fire_in_order_and_fifo_on_ties():
    clock = FakeClock()
    fired = []
    clock.call_later(2.0, lambda: fired.append(("b", clock.now())))
    clock.call_later(1.0, lambda: fired.append(("a", clock.now())))
    clock.call_later(2.0, lambda: fired.append(("c", clock.now())))
    await clock.advance(1.5)
    assert fired == [("a", 1.0)] and clock.now() == 1.5
    await clock.advance(1.0)
    assert fired == [("a", 1.0), ("b", 2.0), ("c", 2.0)]


async def test_cancelled_timer_does_not_fire():
    clock = FakeClock()
    fired = []
    clock.call_later(1.0, lambda: fired.append(1)).cancel()
    await clock.advance(2.0)
    assert fired == []


async def test_sleep_uses_virtual_time():
    clock = FakeClock()
    woke = []

    async def sleeper():
        await clock.sleep(0.5)
        woke.append(clock.now())
        await clock.sleep(0.5)
        woke.append(clock.now())

    task = asyncio.ensure_future(sleeper())
    await clock.advance(0.4)
    assert woke == []
    await clock.advance(1.0)
    assert woke == [0.5, 1.0]
    await task


async def test_wait_for_returns_result_before_timeout():
    clock = FakeClock()
    fut = asyncio.get_running_loop().create_future()
    clock.call_later(0.2, lambda: fut.set_result("ok"))
    task = asyncio.ensure_future(clock.wait_for(fut, timeout=1.0))
    await clock.advance(0.3)
    assert task.result() == "ok"


async def test_wait_for_times_out():
    clock = FakeClock()
    fut = asyncio.get_running_loop().create_future()
    task = asyncio.ensure_future(clock.wait_for(fut, timeout=1.0))
    await clock.advance(0.9)
    assert not task.done()
    await clock.advance(0.2)
    with pytest.raises(TimeoutError):
        task.result()


# --- FakeNetwork -----------------------------------------------------------

def collector(transport, msg_type="PING"):
    inbox = []

    async def handler(msg):
        inbox.append((msg.src, msg.payload))

    transport.on(msg_type, handler)
    return inbox


async def test_delivers_after_delay_and_logs():
    net = FakeNetwork([1, 2, 3], delay=0.01)
    log1 = MemoryLog(1, net.clock.now)
    log2 = MemoryLog(2, net.clock.now)
    t1 = net.transport(1, log1)
    t2 = net.transport(2, log2)
    net.transport(3)
    inbox = collector(t2)

    assert t1.peers == [2, 3]
    await t1.send(2, MessageFactory(1).new("PING", 2, {"n": 1}))
    await net.run(0.005)
    assert inbox == []
    await net.run(0.01)
    assert inbox == [(1, {"n": 1})]
    assert [r["event"] for r in log1.records] == ["msg_send"]
    assert log2.events("msg_recv")[0]["ts"] == pytest.approx(0.01)


async def test_send_to_unknown_peer_raises():
    net = FakeNetwork([1, 2])
    t1 = net.transport(1)
    with pytest.raises(ValueError):
        await t1.send(9, MessageFactory(1).new("PING", 9))


async def test_loss_is_deterministic_for_same_seed():
    async def run(seed):
        net = FakeNetwork([1, 2], seed=seed, drop_rate=0.3)
        t1, t2 = net.transport(1), net.transport(2)
        inbox = collector(t2)
        f = MessageFactory(1)
        for i in range(200):
            await t1.send(2, f.new("PING", 2, {"i": i}))
        await net.run(1.0)
        return [p["i"] for _, p in inbox]

    a, b, c = await run(1), await run(1), await run(2)
    assert a == b
    assert a != c
    assert 100 < len(a) < 180  # ~70% entregues


async def test_crash_and_recovery():
    net = FakeNetwork([1, 2])
    t1 = net.transport(1)
    old2 = net.transport(2)
    old_inbox = collector(old2)
    f1, f2 = MessageFactory(1), MessageFactory(2)

    net.crash(2)
    assert not net.is_up(2)
    await t1.send(2, f1.new("PING", 2))
    await old2.send(1, f2.new("PING", 1))
    await net.run(1.0)
    assert old_inbox == [] and len(net.dropped) == 2

    new2 = net.transport(2)  # recuperação com estado vazio
    new_inbox = collector(new2)
    await t1.send(2, f1.new("PING", 2))
    await old2.send(1, f2.new("PING", 1))  # instância zumbi continua isolada
    await net.run(1.0)
    assert len(new_inbox) == 1 and old_inbox == []


async def test_in_flight_message_dropped_if_receiver_crashes():
    net = FakeNetwork([1, 2], delay=0.1)
    t1, t2 = net.transport(1), net.transport(2)
    inbox = collector(t2)
    await t1.send(2, MessageFactory(1).new("PING", 2))
    await net.run(0.05)
    net.crash(2)
    await net.run(0.1)
    assert inbox == [] and len(net.dropped) == 1


async def test_partition_and_heal():
    net = FakeNetwork([1, 2, 3])
    t = {i: net.transport(i) for i in (1, 2, 3)}
    inbox2, inbox3 = collector(t[2]), collector(t[3])
    f1 = MessageFactory(1)

    net.partition({1, 2}, {3})
    await t[1].send(2, f1.new("PING", 2))
    await t[1].send(3, f1.new("PING", 3))
    await net.run(0.1)
    assert len(inbox2) == 1 and inbox3 == []

    net.heal()
    await t[1].send(3, f1.new("PING", 3))
    await net.run(0.1)
    assert len(inbox3) == 1


async def test_handler_exception_surfaces_in_run():
    net = FakeNetwork([1, 2])
    t1, t2 = net.transport(1), net.transport(2)

    async def boom(msg):
        raise RuntimeError("bug no handler")

    t2.on("PING", boom)
    await t1.send(2, MessageFactory(1).new("PING", 2))
    with pytest.raises(RuntimeError, match="bug no handler"):
        await net.run(0.1)


async def test_request_reply_with_timeout():
    """Padrão que o Bully usa: envia e espera resposta com prazo."""
    net = FakeNetwork([1, 2], delay=0.01)
    t1, t2 = net.transport(1), net.transport(2)
    f1, f2 = MessageFactory(1), MessageFactory(2)
    answered = asyncio.Event()

    async def on_ping(msg):
        await t2.send(1, f2.new("PONG", 1))

    async def on_pong(msg):
        answered.set()

    t2.on("PING", on_ping)
    t1.on("PONG", on_pong)

    async def ask():
        await t1.send(2, f1.new("PING", 2))
        await net.clock.wait_for(answered.wait(), timeout=0.5)
        return net.clock.now()

    task = asyncio.ensure_future(ask())
    await net.run(1.0)
    assert task.result() == pytest.approx(0.02)

    # Sem o nó 2, a mesma espera estoura o prazo
    answered.clear()
    net.crash(2)
    task = asyncio.ensure_future(ask())
    await net.run(1.0)
    with pytest.raises(TimeoutError):
        task.result()
