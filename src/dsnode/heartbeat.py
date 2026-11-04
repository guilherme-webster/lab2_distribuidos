"""Detector de falhas por heartbeat (all-to-all, timeout fixo ou adaptativo).

A cada hb_interval_ms o nó envia HEARTBEAT a todos os peers. Cada peer tem um
timer de timeout reiniciado a cada heartbeat novo; se ele estourar, o peer
passa a ser suspeito (evento `suspect`). Um heartbeat de um suspeito o
restaura (evento `restore`).

Um restore com a mesma encarnação é uma falsa suspeita (o peer nunca caiu);
com encarnação nova, o peer de fato reiniciou. Com hb_adaptive, cada falsa
suspeita soma hb_timeout_step_ms ao timeout daquele peer.

Propriedades (sob sincronia parcial, canais com perda):
- completude forte: um nó que falha é suspeito por todo nó correto em até
  timeout + atraso da rede após a falha, e continua suspeito;
- precisão, timeout fixo: sem perdas e com atraso < timeout - intervalo,
  nenhum nó correto é suspeito; com perdas ou pausas maiores que o timeout
  há falsas suspeitas para sempre, todas desfeitas pelo próximo heartbeat;
- precisão, adaptativo sem teto: depois do GST o intervalo entre heartbeats
  é limitado, então o timeout cresce um número finito de vezes até superar
  esse limite e as falsas suspeitas cessam (precisão eventual, ◇P). Um teto
  limita o tempo de detecção, mas abre mão dessa garantia.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from dsnode.clock import Clock, Timer
from dsnode.config import Config
from dsnode.log import EventLog
from dsnode.messages import Message, MessageFactory, MsgType
from dsnode.transport import Transport

Callback = Callable[[int], None]


@dataclass
class _PeerState:
    last_seen: float
    timeout: float
    # (encarnação, seq) do último heartbeat aceito; a encarnação cresce a cada
    # reinício do remetente, então a comparação de tuplas ordena certo
    last_hb: tuple[int, int] = (-1, -1)
    suspected: bool = False
    suspected_at: float = 0.0
    timer: Timer | None = None


class FailureDetector:
    def __init__(
        self,
        transport: Transport,
        clock: Clock,
        cfg: Config,
        log: EventLog,
        factory: MessageFactory,
    ):
        self.transport = transport
        self.clock = clock
        self.log = log
        self.factory = factory
        self.interval = cfg.hb_interval_ms / 1000
        self.timeout = cfg.hb_timeout_ms / 1000
        self.adaptive = cfg.hb_adaptive
        self.step = cfg.hb_timeout_step_ms / 1000
        self.max_timeout = cfg.hb_timeout_max_ms / 1000 or None
        self._peers: dict[int, _PeerState] = {}
        self._subscribers: list[tuple[Callback, Callback]] = []
        self._seq = 0
        self._sender: asyncio.Task | None = None

    # --- interface pública -------------------------------------------------

    def subscribe(self, on_suspect: Callback, on_restore: Callback) -> None:
        self._subscribers.append((on_suspect, on_restore))

    def is_suspected(self, peer: int) -> bool:
        return self._peers[peer].suspected

    def alive_peers(self) -> set[int]:
        return {p for p, s in self._peers.items() if not s.suspected}

    def timeout_of(self, peer: int) -> float:
        """Timeout atual (s) usado para o peer."""
        return self._peers[peer].timeout

    async def start(self) -> None:
        self.transport.on(MsgType.HEARTBEAT, self._on_heartbeat)
        now = self.clock.now()
        # Todo peer começa como "visto agora": um nó que nunca sobe é
        # suspeito após um timeout, como um que cai
        for peer in self.transport.peers:
            state = _PeerState(last_seen=now, timeout=self.timeout)
            self._peers[peer] = state
            self._arm(peer, state)
        self._sender = asyncio.ensure_future(self._send_loop())

    def stop(self) -> None:
        if self._sender is not None:
            self._sender.cancel()
            self._sender = None
        for state in self._peers.values():
            if state.timer is not None:
                state.timer.cancel()

    # --- envio -------------------------------------------------------------

    async def _send_loop(self) -> None:
        while True:
            self._seq += 1
            payload = {"inc": self.factory.incarnation, "seq": self._seq}
            for peer in self.transport.peers:
                await self.transport.send(peer, self.factory.new(MsgType.HEARTBEAT, peer, payload))
            await self.clock.sleep(self.interval)

    # --- recebimento e timeouts --------------------------------------------

    async def _on_heartbeat(self, msg: Message) -> None:
        state = self._peers.get(msg.src)
        inc, seq = msg.payload.get("inc"), msg.payload.get("seq")
        if state is None or not isinstance(inc, int) or not isinstance(seq, int):
            return
        # Heartbeat atrasado ou duplicado não prova que o peer está vivo agora
        if (inc, seq) <= state.last_hb:
            return
        now = self.clock.now()
        # Mesma encarnação = o peer nunca caiu; -1 = primeiro contato (ele
        # pode só ter subido depois de nós, o que não é falsa suspeita)
        false_suspicion = state.suspected and inc == state.last_hb[0]
        state.last_hb = (inc, seq)
        state.last_seen = now
        if false_suspicion and self.adaptive:
            state.timeout += self.step
            if self.max_timeout is not None:
                state.timeout = min(state.timeout, self.max_timeout)
        self._arm(msg.src, state)
        if state.suspected:
            state.suspected = False
            self.log.emit(
                "restore",
                peer=msg.src,
                suspected_ms=_ms(now - state.suspected_at),
                false_suspicion=false_suspicion,
                timeout_ms=_ms(state.timeout),
            )
            self._notify(msg.src, restore=True)

    def _arm(self, peer: int, state: _PeerState) -> None:
        if state.timer is not None:
            state.timer.cancel()
        state.timer = self.clock.call_later(state.timeout, lambda: self._expire(peer))

    def _expire(self, peer: int) -> None:
        state = self._peers[peer]
        if state.suspected:
            return
        now = self.clock.now()
        state.suspected = True
        state.suspected_at = now
        self.log.emit(
            "suspect", peer=peer, last_seen_ms=_ms(now - state.last_seen), timeout_ms=_ms(state.timeout)
        )
        self._notify(peer, restore=False)

    def _notify(self, peer: int, restore: bool) -> None:
        for on_suspect, on_restore in self._subscribers:
            try:
                (on_restore if restore else on_suspect)(peer)
            except Exception as e:
                # Bug de um inscrito (ex.: Bully) não pode parar o detector
                self.log.emit("handler_error", error=repr(e), source="heartbeat")


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 3)
