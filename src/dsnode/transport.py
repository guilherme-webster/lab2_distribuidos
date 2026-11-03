"""Transporte UDP entre nós: datagramas JSON, um handler por tipo de mensagem.

Semântica do canal (para o modelo do sistema): best-effort, sem garantia de
entrega, ordem ou ausência de duplicação. Quem precisa de confirmação
(Bully) trata isso com timeouts.
"""

import asyncio
import socket
from collections.abc import Awaitable, Callable
from typing import Protocol

from dsnode.config import Address, Config
from dsnode.log import EventLog
from dsnode.messages import Message, MessageError

Handler = Callable[[Message], Awaitable[None]]

# A resolução de nomes usa o relógio real (o transporte UDP só roda de
# verdade); o cache é curto porque um contêiner reiniciado pode mudar de IP
RESOLVE_TIMEOUT = 0.5
RESOLVE_TTL = 2.0


class Transport(Protocol):
    node_id: int
    peers: list[int]

    async def send(self, dst: int, msg: Message) -> None:
        """Best-effort: nunca levanta erro de rede, só registra msg_drop."""
        ...

    def on(self, msg_type: str, handler: Handler) -> None: ...


class UdpTransport:
    def __init__(self, cfg: Config, log: EventLog):
        self.cfg = cfg
        self.log = log
        self.node_id = cfg.node_id
        self.peers = cfg.peer_ids
        self._handlers: dict[str, Handler] = {}
        self._tasks: set[asyncio.Task] = set()
        self._cache: dict[int, tuple[Address, float]] = {}
        self._udp: asyncio.DatagramTransport | None = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._udp, _ = await loop.create_datagram_endpoint(
            lambda: _Protocol(self),
            local_addr=(self.cfg.host, self.cfg.port),
            family=socket.AF_INET,
        )

    def close(self) -> None:
        if self._udp is not None:
            self._udp.close()
            self._udp = None

    def on(self, msg_type: str, handler: Handler) -> None:
        self._handlers[str(msg_type)] = handler

    async def send(self, dst: int, msg: Message) -> None:
        if dst not in self.cfg.peers:
            raise ValueError(f"destino desconhecido: {dst}")
        if self._udp is None:
            raise RuntimeError("transporte não iniciado")
        addr = await self._resolve(dst)
        if addr is None:
            return self._emit("msg_drop", msg, dst, reason="resolve")
        try:
            self._udp.sendto(msg.to_bytes(), addr)
        except OSError as e:
            self._cache.pop(dst, None)
            return self._emit("msg_drop", msg, dst, reason="send_error", error=repr(e))
        self._emit("msg_send", msg, dst)

    async def _resolve(self, dst: int) -> Address | None:
        loop = asyncio.get_running_loop()
        cached = self._cache.get(dst)
        if cached and cached[1] > loop.time():
            return cached[0]
        host, port = self.cfg.peers[dst]
        try:
            infos = await asyncio.wait_for(
                loop.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_DGRAM),
                RESOLVE_TIMEOUT,
            )
        except (OSError, TimeoutError):
            # Nó parado some do DNS do Docker: é uma falha esperada, não um erro
            self._cache.pop(dst, None)
            return None
        addr = infos[0][4][:2]
        self._cache[dst] = (addr, loop.time() + RESOLVE_TTL)
        return addr

    def _on_datagram(self, data: bytes, addr: Address) -> None:
        try:
            msg = Message.from_bytes(data)
        except MessageError as e:
            self.log.emit("msg_invalid", addr=addr, error=str(e))
            return
        if msg.dst != self.node_id or msg.src not in self.cfg.peers:
            self.log.emit("msg_invalid", addr=addr, error="src/dst inesperado", msg_id=msg.msg_id)
            return
        self._emit("msg_recv", msg, msg.src)
        handler = self._handlers.get(str(msg.type))
        if handler is not None:
            task = asyncio.ensure_future(handler(msg))
            self._tasks.add(task)
            task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        # Um bug num handler não pode derrubar o nó; fica registrado no log
        if not task.cancelled() and task.exception() is not None:
            self.log.emit("handler_error", error=repr(task.exception()))

    def _emit(self, event: str, msg: Message, peer: int, **extra) -> None:
        self.log.emit(event, type=msg.type, peer=peer, msg_id=msg.msg_id, **extra)


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, owner: UdpTransport):
        self.owner = owner

    def datagram_received(self, data: bytes, addr: Address) -> None:
        self.owner._on_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:
        # Ex.: ICMP "port unreachable" de um envio anterior a um nó sem processo
        self.owner.log.emit("transport_error", error=repr(exc))
