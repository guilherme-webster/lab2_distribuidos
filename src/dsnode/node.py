"""Ponto de entrada do nó: `python -m dsnode.node`.

Monta os componentes de um nó sobre o transporte UDP: detector de falhas por
heartbeat e, depois, a eleição Bully (inscrita no detector).
"""

import asyncio
import dataclasses
import signal
import time

from dsnode.clock import RealClock
from dsnode.config import Config
from dsnode.heartbeat import FailureDetector
from dsnode.log import EventLog
from dsnode.messages import MessageFactory
from dsnode.transport import UdpTransport


async def run(cfg: Config) -> None:
    log = EventLog(cfg.node_id)
    clock = RealClock()
    # Uma única fábrica por nó, compartilhada pelos algoritmos: msg_ids únicos.
    # Encarnação = instante de início em ms: cresce a cada reinício do nó
    factory = MessageFactory(cfg.node_id, incarnation=time.time_ns() // 1_000_000)
    transport = UdpTransport(cfg, log)
    detector = FailureDetector(transport, clock, cfg, log, factory)
    # Bully: criar aqui com o mesmo transport/clock/cfg/log/factory e o
    # detector, e inscrevê-lo com detector.subscribe(on_suspect, on_restore)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    await transport.start()
    log.emit("node_start", incarnation=factory.incarnation, config=dataclasses.asdict(cfg))
    await detector.start()
    try:
        await stop.wait()
    finally:
        detector.stop()
        transport.close()
        log.emit("node_stop")


def main() -> None:
    asyncio.run(run(Config.from_env()))


if __name__ == "__main__":
    main()
