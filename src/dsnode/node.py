"""Ponto de entrada do nó: `python -m dsnode.node`.

Provisório: só troca PING/PONG com os peers para validar transporte, Docker e
compose. O heartbeat e o Bully serão ligados aqui.
"""

import asyncio
import dataclasses
import signal
import time

from dsnode.clock import RealClock
from dsnode.config import Config
from dsnode.log import EventLog
from dsnode.messages import Message, MessageFactory
from dsnode.transport import UdpTransport

# Provisório: some quando o heartbeat substituir o ping
PING_INTERVAL = 1.0


async def run(cfg: Config) -> None:
    log = EventLog(cfg.node_id)
    clock = RealClock()
    # Encarnação = instante de início em ms: distingue msg_ids após um restart
    factory = MessageFactory(cfg.node_id, incarnation=time.time_ns() // 1_000_000)
    transport = UdpTransport(cfg, log)

    async def on_ping(msg: Message) -> None:
        await transport.send(msg.src, factory.new("PONG", msg.src, {"ping": msg.msg_id}))

    async def on_pong(msg: Message) -> None:
        pass  # msg_recv já fica registrado pelo transporte

    transport.on("PING", on_ping)
    transport.on("PONG", on_pong)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    await transport.start()
    log.emit("node_start", incarnation=factory.incarnation, config=dataclasses.asdict(cfg))

    async def ping_loop() -> None:
        while True:
            for peer in transport.peers:
                await transport.send(peer, factory.new("PING", peer))
            await clock.sleep(PING_INTERVAL)

    pinger = asyncio.ensure_future(ping_loop())
    try:
        await stop.wait()
    finally:
        pinger.cancel()
        transport.close()
        log.emit("node_stop")


def main() -> None:
    asyncio.run(run(Config.from_env()))


if __name__ == "__main__":
    main()
