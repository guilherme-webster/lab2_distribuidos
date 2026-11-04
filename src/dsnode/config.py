"""Configuração do nó, lida exclusivamente de variáveis de ambiente.

O mesmo código roda local (compose) e na nuvem do IC; só o ambiente muda.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

Address = tuple[str, int]


class ConfigError(ValueError):
    """Configuração ausente ou inválida."""


@dataclass(frozen=True)
class Config:
    node_id: int
    # Outros nós do sistema (sem o próprio nó): id -> (host, porta)
    peers: dict[int, Address] = field(default_factory=dict)
    host: str = "0.0.0.0"
    port: int = 9000

    # Detector de falhas (heartbeat)
    hb_interval_ms: int = 200
    hb_timeout_ms: int = 600
    # Timeout adaptativo: cada falsa suspeita soma hb_timeout_step_ms ao
    # timeout daquele peer, até hb_timeout_max_ms (0 = sem teto)
    hb_adaptive: bool = False
    hb_timeout_step_ms: int = 200
    hb_timeout_max_ms: int = 0

    # Eleição Bully
    election_timeout_ms: int = 500
    coord_timeout_ms: int = 1000

    # Injeção de falhas no transporte
    drop_rate: float = 0.0
    delay_ms: int = 0
    jitter_ms: int = 0
    seed: int = 0

    def __post_init__(self) -> None:
        if self.node_id in self.peers:
            raise ConfigError(f"node_id {self.node_id} não pode estar em peers")
        if not 0 < self.port < 65536:
            raise ConfigError(f"porta inválida: {self.port}")
        for name in (
            "hb_interval_ms", "hb_timeout_ms", "hb_timeout_step_ms",
            "election_timeout_ms", "coord_timeout_ms",
        ):
            if getattr(self, name) <= 0:
                raise ConfigError(f"{name} deve ser positivo")
        if self.hb_timeout_ms <= self.hb_interval_ms:
            raise ConfigError("hb_timeout_ms deve ser maior que hb_interval_ms")
        if self.hb_timeout_max_ms and self.hb_timeout_max_ms < self.hb_timeout_ms:
            raise ConfigError("hb_timeout_max_ms deve ser 0 (sem teto) ou >= hb_timeout_ms")
        if not 0.0 <= self.drop_rate <= 1.0:
            raise ConfigError(f"drop_rate fora de [0, 1]: {self.drop_rate}")
        if self.delay_ms < 0 or self.jitter_ms < 0:
            raise ConfigError("delay_ms e jitter_ms não podem ser negativos")

    @property
    def peer_ids(self) -> list[int]:
        return sorted(self.peers)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        """Monta a configuração a partir do ambiente (padrão: os.environ).

        PEERS lista todos os nós, inclusive o próprio, no formato
        "1=node1:9000,2=node2:9000"; assim todos os contêineres recebem a mesma
        string e o próprio nó é removido aqui.
        """
        env = os.environ if env is None else env
        if "NODE_ID" not in env:
            raise ConfigError("NODE_ID é obrigatório")
        node_id = _parse(env, "NODE_ID", int)
        peers = parse_peers(env.get("PEERS", ""))
        peers.pop(node_id, None)
        defaults = cls(node_id=node_id)
        return cls(
            node_id=node_id,
            peers=peers,
            host=env.get("HOST", defaults.host),
            port=_parse(env, "PORT", int, defaults.port),
            hb_interval_ms=_parse(env, "HB_INTERVAL_MS", int, defaults.hb_interval_ms),
            hb_timeout_ms=_parse(env, "HB_TIMEOUT_MS", int, defaults.hb_timeout_ms),
            hb_adaptive=_parse(env, "HB_ADAPTIVE", parse_bool, defaults.hb_adaptive),
            hb_timeout_step_ms=_parse(env, "HB_TIMEOUT_STEP_MS", int, defaults.hb_timeout_step_ms),
            hb_timeout_max_ms=_parse(env, "HB_TIMEOUT_MAX_MS", int, defaults.hb_timeout_max_ms),
            election_timeout_ms=_parse(env, "ELECTION_TIMEOUT_MS", int, defaults.election_timeout_ms),
            coord_timeout_ms=_parse(env, "COORD_TIMEOUT_MS", int, defaults.coord_timeout_ms),
            drop_rate=_parse(env, "DROP_RATE", float, defaults.drop_rate),
            delay_ms=_parse(env, "DELAY_MS", int, defaults.delay_ms),
            jitter_ms=_parse(env, "JITTER_MS", int, defaults.jitter_ms),
            seed=_parse(env, "SEED", int, defaults.seed),
        )


def parse_peers(raw: str) -> dict[int, Address]:
    """Converte "1=node1:9000,2=node2:9000" em {1: ("node1", 9000), ...}."""
    peers: dict[int, Address] = {}
    for entry in filter(None, (e.strip() for e in raw.split(","))):
        try:
            id_part, addr = entry.split("=", 1)
            host, port = addr.rsplit(":", 1)
            peer_id = int(id_part)
            port_num = int(port)
        except ValueError:
            raise ConfigError(f"entrada inválida em PEERS: {entry!r}") from None
        if not host:
            raise ConfigError(f"host vazio em PEERS: {entry!r}")
        if peer_id in peers:
            raise ConfigError(f"id repetido em PEERS: {peer_id}")
        peers[peer_id] = (host, port_num)
    return peers


def parse_bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off", ""):
        return False
    raise ValueError(raw)


def _parse(env: Mapping[str, str], name: str, conv: type, default=None):
    if name not in env:
        return default
    try:
        return conv(env[name])
    except ValueError:
        raise ConfigError(f"{name} inválido: {env[name]!r}") from None
