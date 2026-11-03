#!/usr/bin/env python3
"""Gera um compose.yaml com N nós.

    python scripts/gen_compose.py 5              # escreve compose.yaml
    python scripts/gen_compose.py 9 -o -         # imprime no stdout

Os parâmetros dos algoritmos e da injeção de falhas não ficam fixos no
arquivo: são lidos do ambiente do host na hora do `docker compose up`
(interpolação ${VAR:-padrão}), então os experimentos variam parâmetros sem
regenerar o compose. Os padrões abaixo espelham os do config.py.
"""

import argparse
import sys

IMAGE = "mc714-dsnode:dev"

# Variáveis repassadas do host para todos os nós, com o padrão de config.py
TUNABLES = {
    "HB_INTERVAL_MS": "200",
    "HB_TIMEOUT_MS": "600",
    "ELECTION_TIMEOUT_MS": "500",
    "COORD_TIMEOUT_MS": "1000",
    "DROP_RATE": "0.0",
    "DELAY_MS": "0",
    "JITTER_MS": "0",
    "SEED": "0",
}


def render(n: int, port: int = 9000) -> str:
    if n < 1:
        raise ValueError("N deve ser >= 1")
    peers = ",".join(f"{i}=node{i}:{port}" for i in range(1, n + 1))
    lines = [
        f"# Gerado por scripts/gen_compose.py {n} -- não edite à mão",
        "name: mc714",
        "",
        "x-node: &node",
        f"  image: {IMAGE}",
        "  build: .",
        "  init: true",
        "  restart: \"no\"",
        "  stop_grace_period: 2s",
        "",
        "x-env: &env",
        f"  PEERS: \"{peers}\"",
        f"  PORT: \"{port}\"",
    ]
    lines += [f"  {k}: \"${{{k}:-{v}}}\"" for k, v in TUNABLES.items()]
    lines += ["", "services:"]
    for i in range(1, n + 1):
        lines += [
            f"  node{i}:",
            "    <<: *node",
            f"    hostname: node{i}",
            "    environment:",
            "      <<: *env",
            f"      NODE_ID: \"{i}\"",
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("n", type=int, help="número de nós")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("-o", "--out", default="compose.yaml", help='arquivo de saída ("-" = stdout)')
    args = parser.parse_args(argv)
    text = render(args.n, args.port)
    if args.out == "-":
        sys.stdout.write(text)
    else:
        with open(args.out, "w") as f:
            f.write(text)


if __name__ == "__main__":
    main()
