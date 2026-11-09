#!/usr/bin/env python3
"""Mede mensagens do Bully no simulador, controlando o primeiro detector.

    .venv/bin/python scripts/run_bully_messages.py

Usa os algoritmos reais com FakeNetwork/FakeClock, sem Docker ou rede UDP.
"""

import argparse
import asyncio
import csv
import gzip
import json
import platform
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from dsnode.bully import Bully
from dsnode.config import Config
from dsnode.heartbeat import FailureDetector
from dsnode.log import MemoryLog
from dsnode.messages import MessageFactory, MsgType
from tests.sim import FakeNetwork

EXPERIMENT = "bully-messages-sim"
WARMUP_S = 10
DURATION_S = 8


class ControlledNotifications:
    """Atrasa callbacks dos demais nós, preservando as suspeitas reais.

    Todos precisam suspeitar de N para aceitar um coordenador menor. Apenas
    o detector escolhido notifica imediatamente; os demais notificam após
    2 s, quando a eleição já terminou e N deixou de ser seu líder.
    """

    def __init__(self, detector, clock, first):
        self.detector = detector
        self.clock = clock
        self.first = first
        self.factory = detector.factory

    def is_suspected(self, peer):
        return self.detector.is_suspected(peer)

    def subscribe(self, on_suspect, on_restore):
        def notify(peer):
            if self.first:
                on_suspect(peer)
            else:
                self.clock.call_later(2, lambda: on_suspect(peer))
        self.detector.subscribe(notify, on_restore)


async def measure(n: int, case: str, seed: int) -> tuple[dict, list[dict], dict]:
    """Inicializa, derruba N e observa a eleição inteira e sua estabilidade."""
    initiator = n - 1 if case == "best" else 1
    net = FakeNetwork(list(range(1, n + 1)), seed=seed, delay=0.005)
    nodes, detectors, logs, configs = {}, {}, {}, {}
    try:
        for node_id in net.node_ids:
            cfg = Config.from_env({
                "NODE_ID": str(node_id), "SEED": str(seed),
                "HB_INTERVAL_MS": "200",
                "HB_TIMEOUT_MS": "600",
                "ELECTION_TIMEOUT_MS": "500", "COORD_TIMEOUT_MS": "1000",
                "HB_ADAPTIVE": "false", "DROP_RATE": "0", "DELAY_MS": "5",
            })
            configs[node_id] = {
                "HB_TIMEOUT_MS": cfg.hb_timeout_ms,
                "HB_INTERVAL_MS": cfg.hb_interval_ms,
                "ELECTION_TIMEOUT_MS": cfg.election_timeout_ms,
                "COORD_TIMEOUT_MS": cfg.coord_timeout_ms,
            }
            log = logs[node_id] = MemoryLog(node_id, net.clock.now)
            transport = net.transport(node_id, log)
            factory = MessageFactory(node_id)
            detector = detectors[node_id] = FailureDetector(transport, net.clock, cfg, log, factory)
            controlled = ControlledNotifications(detector, net.clock, node_id == initiator)
            node = nodes[node_id] = Bully(transport, net.clock, cfg, log, controlled)
            await detector.start()
            await node.start()
        await net.run(WARMUP_S)
        assert all(node.leader() == n for node in nodes.values()), "sem acordo inicial"
        # Índice separa as mensagens iniciais, mesmo se houver timestamps iguais.
        first = len(net.sent)
        nodes[n].stop()
        detectors[n].stop()
        net.crash(n)
        fault = {"ts": net.clock.now(), "node": n, "event": "experiment_crash"}
        await net.run(DURATION_S)
        assert all(nodes[i].leader() == n - 1 for i in range(1, n)), "sem acordo final"
        records = sorted([fault] + [r for log in logs.values() for r in log.records], key=lambda r: r["ts"])
        assert not any(r["event"] == "handler_error" for r in records)
        starts = [r for r in records if r["ts"] >= WARMUP_S and r["event"] == "election_start"]
        assert starts and starts[0]["node"] == initiator
        assert starts[0]["reason"] == "leader_suspected"
        # Todos suspeitam, mas só o escolhido recebe o callback imediato.
        # Os demais candidatos entram por ELECTION.
        suspects = [r["node"] for r in records if r["event"] == "suspect" and r["peer"] == n]
        assert set(suspects) == set(range(1, n)), suspects
        assert [r["node"] for r in starts if r["reason"] == "leader_suspected"] == [initiator]
        messages = [m for m in net.sent[first:] if m.type != MsgType.HEARTBEAT]
        counts = Counter(m.type for m in messages)
        ids = {m.msg_id for m in messages}
        assert len(ids) == len(messages)
        row = {
            "N": n, "case": case, "seed": seed, "initiator": initiator,
            "ELECTION": counts[MsgType.ELECTION], "ANSWER": counts[MsgType.ANSWER],
            "COORDINATOR": counts[MsgType.COORDINATOR], "total": len(messages),
            "to_failed_leader": sum(m.dst == n for m in messages),
            "delivered": sum(m.msg_id in ids for m in net.delivered),
            "final_leader": n - 1,
        }
        meta = {
            "experiment": EXPERIMENT, "backend": "FakeNetwork/FakeClock",
            "N": n, "case": case, "seed": seed, "initiator": initiator,
            "warmup_s": WARMUP_S, "duration_s": DURATION_S,
            "delay_ms": 5, "jitter_ms": 0, "drop_rate": 0,
            "other_detector_callback_delay_s": 2,
            "node_configs": configs,
            "counting": "Todos os envios ELECTION/ANSWER/COORDINATOR após o crash, inclusive ao nó morto",
            "complete": True,
        }
        return row, records, meta
    finally:
        for node in nodes.values():
            node.stop()
        for detector in detectors.values():
            detector.stop()


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--out", type=Path, default=ROOT / "experiments")
    args = parser.parse_args()
    if args.seeds < 1:
        parser.error("--seeds deve ser positivo")
    raw = args.out / "raw" / EXPERIMENT
    results = args.out / "results" / EXPERIMENT
    raw.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)
    env = {
        "python": platform.python_version(), "platform": platform.platform(),
        "cpu": platform.processor(),
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "git_status": subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True),
    }
    (raw / "env.json").write_text(json.dumps(env, indent=2) + "\n")
    rows = []
    for seed in range(1, args.seeds + 1):
        for n in (3, 5, 7, 9):
            for case in ("best", "worst"):
                row, records, meta = await measure(n, case, seed)
                dest = raw / f"N={n}__case={case}" / f"seed-{seed:02d}"
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
                payload = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records)
                (dest / "logs.jsonl.gz").write_bytes(gzip.compress(payload.encode(), mtime=0))
                rows.append(row)
        print(f"seed {seed}/{args.seeds}: ok", flush=True)
    write_csv(results / "runs.csv", rows)
    summary = []
    for n in (3, 5, 7, 9):
        for case in ("best", "worst"):
            group = [r for r in rows if r["N"] == n and r["case"] == case]
            values = [r["total"] for r in group]
            summary.append({
                "N": n, "case": case, "runs": len(group),
                **{key: statistics.mean(r[key] for r in group) for key in ("ELECTION", "ANSWER", "COORDINATOR")},
                "mean": statistics.mean(values), "std": statistics.stdev(values) if len(values) > 1 else 0,
                **{f"p{p}": sorted(values)[max(0, (p * len(values) + 99) // 100 - 1)] for p in (50, 95, 99)},
                "min": min(values), "max": max(values),
            })
    write_csv(results / "summary.csv", summary)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 4))
    for case, label in (("best", "Melhor caso: detector N−1"), ("worst", "Pior caso: detector 1")):
        group = [r for r in summary if r["case"] == case]
        ax.plot([r["N"] for r in group], [r["mean"] for r in group], "o-", label=label)
    ax.set(xlabel="Número inicial de nós (N)", ylabel="Mensagens enviadas por eleição", xticks=[3, 5, 7, 9])
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(results / "messages.png", dpi=180)
    plt.close(fig)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
