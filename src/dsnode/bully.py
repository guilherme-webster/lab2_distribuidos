"""Eleição Bully com timeouts e estado local por nó.

O maior ID acessível vence sob comunicação estável e timeouts adequados.
Perdas e partições podem causar líderes divergentes; não há quórum nem
garantia de líder único nesses cenários. election_id correlaciona uma
tentativa, sem estabelecer uma ordem global entre eleições.
"""

import asyncio
from collections.abc import Coroutine
from enum import Enum, auto

from dsnode.clock import Clock, Timer
from dsnode.config import Config
from dsnode.heartbeat import FailureDetector
from dsnode.log import EventLog
from dsnode.messages import Message, MessageFactory, MsgType
from dsnode.transport import Transport


class _State(Enum):
    IDLE = auto()
    WAIT_ANSWER = auto()
    WAIT_COORDINATOR = auto()


class Bully:
    def __init__(
        self,
        transport: Transport,
        clock: Clock,
        cfg: Config,
        log: EventLog,
        detector: FailureDetector,
        factory: MessageFactory | None = None,
    ):
        self.transport = transport
        self.clock = clock
        self.log = log
        self.detector = detector
        # Por padrão, compartilha os IDs e a encarnação com o heartbeat.
        self.factory = factory if factory is not None else detector.factory
        self.node_id = transport.node_id
        self.election_timeout = cfg.election_timeout_ms / 1000
        self.coord_timeout = cfg.coord_timeout_ms / 1000
        self._state = _State.IDLE
        self._leader: int | None = None
        self._election_id: str | None = None
        self._seq = 0
        self._timer: Timer | None = None
        self._tasks: set[asyncio.Task] = set()
        self._running = False
        self._subscribed = False

    async def start(self) -> None:
        """Registra handlers e convoca a eleição inicial; não espera seu fim.

        O transporte e o detector devem ter sido iniciados antes.
        """
        if self._running:
            return
        self._running = True
        self.transport.on(MsgType.ELECTION, self._on_election)
        self.transport.on(MsgType.ANSWER, self._on_answer)
        self.transport.on(MsgType.COORDINATOR, self._on_coordinator)
        if not self._subscribed:
            self.detector.subscribe(self._on_suspect, self._on_restore)
            self._subscribed = True
        await self.start_election("startup")

    def stop(self) -> None:
        """Cancela timers e tarefas; handlers e callbacks passam a ignorar eventos."""
        self._running = False
        self._cancel_timer()
        for task in self._tasks:
            task.cancel()
        self._state = _State.IDLE
        self._leader = None
        self._election_id = None

    def leader(self) -> int | None:
        return self._leader

    async def start_election(self, reason: str) -> None:
        """Inicia uma tentativa, preservando outra que já esteja em andamento."""
        if not self._running or self._state is not _State.IDLE:
            return
        self._seq += 1
        election_id = f"{self.node_id}-{self.factory.incarnation}-{self._seq}"
        self._election_id = election_id
        self._state = _State.WAIT_ANSWER
        self.log.emit("election_start", election_id=election_id, reason=reason)
        self._set_leader(None, election_id)
        higher = [peer for peer in self.transport.peers if peer > self.node_id]
        if not higher:
            await self._become_leader(election_id)
            return
        self._arm(self.election_timeout, election_id, _State.WAIT_ANSWER)
        # Suspeita não é prova de falha: também consultamos os suspeitos.
        for peer in higher:
            if not self._active(election_id, _State.WAIT_ANSWER):
                break
            await self._send(MsgType.ELECTION, peer, election_id)

    async def _on_election(self, msg: Message) -> None:
        if not self._valid(msg) or msg.src >= self.node_id:
            return
        await self._send(MsgType.ANSWER, msg.src, msg.payload["election_id"])
        if self._leader == self.node_id:
            # Reenvio permite recuperar um anúncio perdido sem outra eleição.
            await self._announce(self._election_id)
        else:
            await self.start_election("election_received")

    async def _on_answer(self, msg: Message) -> None:
        if not self._valid(msg) or msg.src <= self.node_id:
            return
        election_id = msg.payload["election_id"]
        if not self._active(election_id, _State.WAIT_ANSWER):
            return
        self.log.emit("answer_recv", election_id=election_id, peer=msg.src)
        self._state = _State.WAIT_COORDINATOR
        # Respostas adicionais não adiam indefinidamente este prazo.
        self._arm(self.coord_timeout, election_id, _State.WAIT_COORDINATOR)

    async def _on_coordinator(self, msg: Message) -> None:
        if not self._valid(msg):
            return
        if msg.src < self.node_id:
            if self._leader == self.node_id:
                await self._announce(self._election_id)
            else:
                await self.start_election("lower_coordinator")
            return
        if self._leader is not None and msg.src < self._leader:
            if not self.detector.is_suspected(self._leader):
                # Um anúncio atrasado não substitui um líder maior ainda vivo.
                return
        # O anúncio pode pertencer à tentativa de outro nó.
        self._cancel_timer()
        self._state = _State.IDLE
        self._election_id = msg.payload["election_id"]
        self._set_leader(msg.src, self._election_id)
        # Um anúncio atrasado não deve fixar um líder já suspeito para sempre.
        if self.detector.is_suspected(msg.src):
            await self.start_election("coordinator_suspected")

    def _on_suspect(self, peer: int) -> None:
        if self._running and peer == self._leader:
            self._spawn(self.start_election("leader_suspected"))

    def _on_restore(self, peer: int) -> None:
        if self._running and (self._leader is None or peer > self._leader):
            self._spawn(self.start_election("peer_restored"))

    def _valid(self, msg: Message) -> bool:
        election_id = msg.payload.get("election_id")
        return (
            self._running
            and msg.dst == self.node_id
            and msg.src in self.transport.peers
            and isinstance(election_id, str)
            and bool(election_id)
        )

    def _active(self, election_id: str, state: _State) -> bool:
        return self._running and self._election_id == election_id and self._state is state

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _arm(self, timeout: float, election_id: str, state: _State) -> None:
        self._cancel_timer()
        self._timer = self.clock.call_later(
            timeout, lambda: self._spawn(self._expire(election_id, state))
        )

    async def _expire(self, election_id: str, state: _State) -> None:
        if not self._active(election_id, state):
            return
        if state is _State.WAIT_ANSWER:
            await self._become_leader(election_id)
        else:
            self._state = _State.IDLE
            await self.start_election("coordinator_timeout")

    async def _become_leader(self, election_id: str) -> None:
        if not self._active(election_id, _State.WAIT_ANSWER):
            return
        self._cancel_timer()
        self._state = _State.IDLE
        self._set_leader(self.node_id, election_id)
        await self._announce(election_id)

    async def _announce(self, election_id: str) -> None:
        for peer in self.transport.peers:
            if not self._running or self._leader != self.node_id or self._election_id != election_id:
                break
            await self._send(MsgType.COORDINATOR, peer, election_id)
            self.log.emit("coordinator_sent", election_id=election_id, peer=peer)

    async def _send(self, msg_type: str, peer: int, election_id: str) -> None:
        await self.transport.send(peer, self.factory.new(msg_type, peer, {"election_id": election_id}))

    def _set_leader(self, leader: int | None, election_id: str) -> None:
        if self._leader != leader:
            self._leader = leader
            self.log.emit("leader_change", leader=leader, election_id=election_id)

    def _spawn(self, coro: Coroutine) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self.log.emit("handler_error", source="bully", error=repr(task.exception()))
