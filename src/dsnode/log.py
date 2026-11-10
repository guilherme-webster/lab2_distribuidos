"""Log de eventos estruturados: um objeto JSON por linha.

Esses logs são a evidência de correção (scripts/check_heartbeat.py e
scripts/bully_metrics.py) e a fonte das métricas dos experimentos; os nomes
dos eventos fazem parte do contrato.
"""

import json
import sys
import time
from collections.abc import Callable
from typing import TextIO

Record = dict
Sink = Callable[[Record], None]

RESERVED = ("ts", "node", "event")


def stream_sink(out: TextIO) -> Sink:
    """Escreve cada registro como uma linha JSON, com flush imediato.

    O flush evita que o buffer do processo atrase ou perca linhas em
    `docker logs` quando o contêiner é morto.
    """

    def write(record: Record) -> None:
        out.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")
        out.flush()

    return write


class EventLog:
    def __init__(
        self,
        node_id: int,
        clock: Callable[[], float] = time.time,
        sink: Sink | None = None,
    ):
        # clock é relógio de parede (segundos): os nós rodam no mesmo host,
        # então os ts são comparáveis entre contêineres
        self.node_id = node_id
        self.clock = clock
        self.sink = sink or stream_sink(sys.stdout)

    def emit(self, event: str, /, **fields) -> Record:
        clash = [k for k in RESERVED if k in fields]
        if clash:
            raise ValueError(f"campos reservados no evento {event!r}: {clash}")
        record = {"ts": round(self.clock(), 6), "node": self.node_id, "event": event, **fields}
        self.sink(record)
        return record


class MemoryLog(EventLog):
    """EventLog que guarda os registros em memória, para testes."""

    def __init__(self, node_id: int, clock: Callable[[], float] = time.time):
        self.records: list[Record] = []
        super().__init__(node_id, clock, self.records.append)

    def events(self, name: str) -> list[Record]:
        return [r for r in self.records if r["event"] == name]
