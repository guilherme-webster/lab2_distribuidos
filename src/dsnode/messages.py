"""Mensagens trocadas entre os nós e sua (de)serialização em JSON."""

import json
from dataclasses import dataclass, field
from enum import StrEnum

# Limite prático de um datagrama UDP (64 KiB menos cabeçalhos)
MAX_DATAGRAM = 65_000


class MsgType(StrEnum):
    HEARTBEAT = "HEARTBEAT"
    ELECTION = "ELECTION"
    ANSWER = "ANSWER"
    COORDINATOR = "COORDINATOR"


class MessageError(ValueError):
    """Datagrama que não é uma mensagem válida."""


@dataclass(frozen=True)
class Message:
    type: str
    src: int
    dst: int
    msg_id: str
    payload: dict = field(default_factory=dict)

    def to_bytes(self) -> bytes:
        data = json.dumps(
            {
                "type": str(self.type),
                "src": self.src,
                "dst": self.dst,
                "msg_id": self.msg_id,
                "payload": self.payload,
            },
            separators=(",", ":"),
        ).encode()
        if len(data) > MAX_DATAGRAM:
            raise MessageError(f"mensagem com {len(data)} bytes excede o datagrama")
        return data

    @classmethod
    def from_bytes(cls, data: bytes) -> "Message":
        try:
            obj = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise MessageError(f"JSON inválido: {e}") from None
        if not isinstance(obj, dict):
            raise MessageError("mensagem deve ser um objeto JSON")
        _check(obj, "type", str)
        _check(obj, "src", int)
        _check(obj, "dst", int)
        _check(obj, "msg_id", str)
        payload = obj.get("payload", {})
        if not isinstance(payload, dict):
            raise MessageError("payload deve ser um objeto")
        return cls(obj["type"], obj["src"], obj["dst"], obj["msg_id"], payload)


class MessageFactory:
    """Cria mensagens de um nó com msg_id único: "<nó>.<encarnação>.<seq>".

    A encarnação diferencia ids de um nó que reiniciou (crash-recovery), já que
    o contador volta a zero.
    """

    def __init__(self, node_id: int, incarnation: int = 0):
        self.node_id = node_id
        self.incarnation = incarnation
        self._seq = 0

    def new(self, type: str, dst: int, payload: dict | None = None) -> Message:
        self._seq += 1
        msg_id = f"{self.node_id}.{self.incarnation}.{self._seq}"
        return Message(type, self.node_id, dst, msg_id, payload or {})


def _check(obj: dict, key: str, typ: type) -> None:
    value = obj.get(key)
    # bool é subclasse de int; não aceitamos true/false como id
    if not isinstance(value, typ) or isinstance(value, bool):
        raise MessageError(f"campo {key!r} ausente ou com tipo inválido")
    if typ is str and not value:
        raise MessageError(f"campo {key!r} vazio")
