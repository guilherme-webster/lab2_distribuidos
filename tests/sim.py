"""Simulador determinístico para testar os algoritmos sem rede nem Docker.

- FakeClock: o tempo só avança quando o teste manda (FakeNetwork.run).
- FakeNetwork: entrega mensagens entre FakeTransports com atraso, perda
  (semeada), crash, recuperação e partições.
- FakeTransport: mesma interface do transporte real (send / on).

Uso típico:

    net = FakeNetwork([1, 2, 3], seed=7, delay=0.005)
    t1 = net.transport(1, log=MemoryLog(1, net.clock.now))
    ...
    await net.run(2.0)       # avança 2 s simulados
    net.crash(3)             # nó 3 para de enviar e receber
    t3 = net.transport(3)    # recuperação: nova instância, estado vazio

Limitação: crash() isola o nó na rede, mas não cancela as tarefas do
algoritmo daquela instância; ela vira um "zumbi" sem comunicação. Verifique
propriedades só nos nós vivos (ou pare a instância antiga no teste).
"""

import asyncio
import heapq
import itertools
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from dsnode.log import EventLog
from dsnode.messages import Message

T = TypeVar("T")
Handler = Callable[[Message], Awaitable[None]]

# Rodadas do event loop para tarefas prontas avançarem entre dois instantes
# simulados (cadeias de await dentro de um handler, criação de tarefas, etc.)
SETTLE_ROUNDS = 50


class FakeTimer:
    def __init__(self, deadline: float, callback: Callable[[], None]):
        self.deadline = deadline
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class FakeClock:
    """Implementa dsnode.clock.Clock com tempo virtual."""

    def __init__(self, start: float = 0.0):
        self._now = start
        self._timers: list[tuple[float, int, FakeTimer]] = []
        self._seq = itertools.count()

    def now(self) -> float:
        return self._now

    def call_later(self, delay: float, callback: Callable[[], None]) -> FakeTimer:
        timer = FakeTimer(self._now + max(delay, 0.0), callback)
        # seq desempata timers no mesmo instante em ordem de criação (FIFO)
        heapq.heappush(self._timers, (timer.deadline, next(self._seq), timer))
        return timer

    async def sleep(self, seconds: float) -> None:
        fut = asyncio.get_running_loop().create_future()
        timer = self.call_later(seconds, lambda: fut.done() or fut.set_result(None))
        try:
            await fut
        finally:
            timer.cancel()

    async def wait_for(self, aw: Awaitable[T], timeout: float) -> T:
        task = asyncio.ensure_future(aw)
        expired = False

        def expire() -> None:
            nonlocal expired
            if not task.done():
                expired = True
                task.cancel()

        timer = self.call_later(timeout, expire)
        try:
            return await task
        except asyncio.CancelledError:
            if expired:
                raise TimeoutError from None
            raise
        finally:
            timer.cancel()

    async def advance(self, seconds: float) -> None:
        """Avança o tempo, disparando em ordem os timers que vencem no caminho."""
        target = self._now + seconds
        await _settle()
        while self._timers and self._timers[0][0] <= target:
            _, _, timer = heapq.heappop(self._timers)
            if timer.cancelled:
                continue
            self._now = timer.deadline
            timer.callback()
            await _settle()
        self._now = target


async def _settle() -> None:
    for _ in range(SETTLE_ROUNDS):
        await asyncio.sleep(0)


class FakeTransport:
    def __init__(self, network: "FakeNetwork", node_id: int, log: EventLog | None):
        self.network = network
        self.node_id = node_id
        self.peers = [p for p in network.node_ids if p != node_id]
        self.log = log
        self._handlers: dict[str, Handler] = {}

    def on(self, msg_type: str, handler: Handler) -> None:
        self._handlers[str(msg_type)] = handler

    async def send(self, dst: int, msg: Message) -> None:
        if dst not in self.peers:
            raise ValueError(f"destino desconhecido: {dst}")
        self._emit("msg_send", msg, dst)
        self.network._submit(self, dst, msg)

    def _deliver(self, msg: Message) -> None:
        self._emit("msg_recv", msg, msg.src)
        handler = self._handlers.get(str(msg.type))
        if handler is not None:
            self.network._spawn(handler(msg))

    def _emit(self, event: str, msg: Message, peer: int, **extra) -> None:
        if self.log is not None:
            self.log.emit(event, type=msg.type, peer=peer, msg_id=msg.msg_id, **extra)


class FakeNetwork:
    def __init__(
        self,
        node_ids: list[int],
        seed: int = 0,
        delay: float = 0.001,
        jitter: float = 0.0,
        drop_rate: float = 0.0,
        clock: FakeClock | None = None,
    ):
        self.node_ids = sorted(node_ids)
        self.clock = clock or FakeClock()
        self.rng = random.Random(seed)
        self.delay = delay
        self.jitter = jitter
        self.drop_rate = drop_rate
        self._nodes: dict[int, FakeTransport] = {}
        self._groups: dict[int, int] | None = None
        self._errors: list[BaseException] = []
        self._tasks: set[asyncio.Task] = set()
        # Rastro para contar mensagens nos testes (ex.: complexidade do Bully)
        self.sent: list[Message] = []
        self.delivered: list[Message] = []
        self.dropped: list[Message] = []

    # --- ciclo de vida dos nós ---------------------------------------------

    def transport(self, node_id: int, log: EventLog | None = None) -> FakeTransport:
        """Cria (ou recria, após crash) o transporte de um nó."""
        if node_id not in self.node_ids:
            raise ValueError(f"nó desconhecido: {node_id}")
        t = FakeTransport(self, node_id, log)
        self._nodes[node_id] = t
        return t

    def crash(self, node_id: int) -> None:
        self._nodes.pop(node_id, None)

    def is_up(self, node_id: int) -> bool:
        return node_id in self._nodes

    def partition(self, *groups: set[int] | list[int]) -> None:
        """Só nós do mesmo grupo se comunicam; nós fora de todo grupo ficam isolados."""
        self._groups = {n: i for i, g in enumerate(groups) for n in g}

    def heal(self) -> None:
        self._groups = None

    # --- execução ----------------------------------------------------------

    async def run(self, seconds: float) -> None:
        """Avança o tempo simulado e propaga exceções levantadas nos handlers."""
        await self.clock.advance(seconds)
        if self._errors:
            err, self._errors = self._errors[0], []
            raise err

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self._errors.append(task.exception())

    # --- entrega -----------------------------------------------------------

    def _connected(self, a: int, b: int) -> bool:
        if self._groups is None:
            return True
        ga, gb = self._groups.get(a), self._groups.get(b)
        return ga is not None and ga == gb

    def _submit(self, sender: FakeTransport, dst: int, msg: Message) -> None:
        self.sent.append(msg)
        # Instância antiga de um nó que caiu (ou foi recriado) não envia nada
        if self._nodes.get(sender.node_id) is not sender:
            return self._drop(sender, msg, dst, "sender_down")
        if not self._connected(sender.node_id, dst):
            return self._drop(sender, msg, dst, "partition")
        if self.rng.random() < self.drop_rate:
            return self._drop(sender, msg, dst, "loss")
        latency = self.delay + self.rng.uniform(0, self.jitter)
        self.clock.call_later(latency, lambda: self._arrive(sender, dst, msg))

    def _arrive(self, sender: FakeTransport, dst: int, msg: Message) -> None:
        receiver = self._nodes.get(dst)
        if receiver is None:
            return self._drop(sender, msg, dst, "receiver_down")
        # Partição criada enquanto a mensagem estava em trânsito
        if not self._connected(sender.node_id, dst):
            return self._drop(sender, msg, dst, "partition")
        self.delivered.append(msg)
        receiver._deliver(msg)

    def _drop(self, sender: FakeTransport, msg: Message, dst: int, reason: str) -> None:
        self.dropped.append(msg)
        sender._emit("msg_drop", msg, dst, reason=reason)
