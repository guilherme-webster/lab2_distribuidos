#!/usr/bin/env python3
"""Verifica as propriedades do detector de heartbeat a partir dos logs.

    python scripts/check_heartbeat.py logs.jsonl [faults.jsonl ...] [--json out.json]

Lê eventos JSON (um por linha) dos nós e registros de falhas injetadas:

    {"ts": ..., "event": "fault", "action": "kill|stop|pause|unpause|start", "target": 4}

O estado real de cada nó (no ar ou não) vem de node_start/node_stop e das
falhas; ele é comparado com o que os detectores concluíram (suspect/restore):

- completude: todo nó que cai (kill/stop) e fica fora por tempo suficiente é
  suspeito por todo observador no ar em até timeout + folga, e continua
  suspeito enquanto estiver fora (permanência);
- precisão: uma suspeita é justificada se o suspeito, ou o próprio observador
  (ex.: pausado), esteve fora em algum instante na janela de timeout + folga
  que a antecede; senão é falsa;
- recuperação: quando um nó suspeito volta, todo observador no ar o restaura
  em até --recovery-bound.

Sai com código 1 se completude ou recuperação forem violadas (ou, com
--strict-accuracy, se houver falsa suspeita). As métricas (latência de
detecção, taxa de falsas suspeitas, latência de restore) vão para --json.
"""

import argparse
import json
import math
import sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_INTERVAL_MS = 200
DEFAULT_TIMEOUT_MS = 600
DOWN_ACTIONS = {"kill", "stop", "pause"}
CRASH_ACTIONS = {"kill", "stop"}


# --- leitura ---------------------------------------------------------------

def load(paths: list[str | Path]) -> tuple[list[dict], int]:
    """Junta os arquivos em uma lista ordenada por ts; conta linhas inválidas."""
    records, garbage = [], 0
    for path in paths:
        for line in Path(path).read_text().splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                garbage += bool(line.strip())
                continue
            if isinstance(record, dict) and "ts" in record and "event" in record:
                records.append(record)
            else:
                garbage += 1
    records.sort(key=lambda r: r["ts"])
    return records, garbage


# --- estado real dos nós ---------------------------------------------------

class Timeline:
    """Quando cada nó esteve no ar, segundo node_start/node_stop e as falhas."""

    def __init__(self, records: list[dict]):
        changes: dict[int, list[tuple[float, bool]]] = defaultdict(list)
        for r in records:
            if r["event"] == "node_start":
                changes[r["node"]].append((r["ts"], True))
            elif r["event"] == "node_stop":
                changes[r["node"]].append((r["ts"], False))
            elif r["event"] == "fault":
                if r["action"] in DOWN_ACTIONS:
                    changes[r["target"]].append((r["ts"], False))
                elif r["action"] == "unpause":
                    changes[r["target"]].append((r["ts"], True))
                # "start" não muda nada: o nó só está no ar no node_start
        self._ts = {n: [t for t, _ in c] for n, c in changes.items()}
        self._up = {n: [u for _, u in c] for n, c in changes.items()}

    def up_at(self, node: int, t: float) -> bool:
        i = bisect_right(self._ts.get(node, []), t)
        return i > 0 and self._up[node][i - 1]

    def down_during(self, node: int, a: float, b: float) -> bool:
        """O nó esteve fora em algum instante de [a, b]?"""
        if not self.up_at(node, a):
            return True
        ts, up = self._ts.get(node, []), self._up.get(node, [])
        lo, hi = bisect_right(ts, a), bisect_right(ts, b)
        return any(not u for u in up[lo:hi])

    def next_up(self, node: int, t: float) -> float:
        ts, up = self._ts.get(node, []), self._up.get(node, [])
        for i in range(bisect_right(ts, t), len(ts)):
            if up[i]:
                return ts[i]
        return math.inf


# --- relatório -------------------------------------------------------------

@dataclass
class Report:
    nodes: list[int]
    duration_s: float
    garbage_lines: int
    detections: list[dict] = field(default_factory=list)
    completeness_violations: list[dict] = field(default_factory=list)
    permanence_violations: list[dict] = field(default_factory=list)
    false_suspicions: list[dict] = field(default_factory=list)
    justified_suspicions: int = 0
    recoveries: list[dict] = field(default_factory=list)
    recovery_violations: list[dict] = field(default_factory=list)
    strict_accuracy: bool = False

    @property
    def false_suspicion_rate(self) -> float:
        """Falsas suspeitas por par (observador, suspeito) por minuto."""
        pairs = len(self.nodes) * (len(self.nodes) - 1)
        minutes = self.duration_s / 60
        return len(self.false_suspicions) / (pairs * minutes) if pairs and minutes else 0.0

    @property
    def ok(self) -> bool:
        violated = self.completeness_violations or self.permanence_violations or self.recovery_violations
        return not violated and not (self.strict_accuracy and self.false_suspicions)

    def to_dict(self) -> dict:
        return {**asdict(self), "false_suspicion_rate": self.false_suspicion_rate, "ok": self.ok}


def check(
    records: list[dict],
    slack: float = 0.5,
    recovery_bound: float = 2.0,
    strict_accuracy: bool = False,
    garbage: int = 0,
) -> Report:
    timeline = Timeline(records)
    starts = [r for r in records if r["event"] == "node_start"]
    configs = {r["node"]: r.get("config", {}) for r in starts}
    nodes = sorted(
        {r["node"] for r in starts}
        | {r["target"] for r in records if r["event"] == "fault"}
        | {int(p) for cfg in configs.values() for p in cfg.get("peers", {})}
    )
    first_up = max((min(r["ts"] for r in starts if r["node"] == n) for n in configs), default=0.0)
    end = records[-1]["ts"] if records else 0.0
    report = Report(
        nodes=nodes, duration_s=max(end - first_up, 0.0), garbage_lines=garbage,
        strict_accuracy=strict_accuracy,
    )

    # Visão de cada observador sobre cada peer, em ordem de tempo
    views: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for r in records:
        if r["event"] in ("suspect", "restore"):
            views[(r["node"], r["peer"])].append(r)

    def base_timeout(p: int) -> float:
        return configs.get(p, {}).get("hb_timeout_ms", DEFAULT_TIMEOUT_MS) / 1000

    def last_view(p: int, q: int, t: float) -> dict | None:
        before = [r for r in views[(p, q)] if r["ts"] <= t]
        return before[-1] if before else None

    def timeout_at(p: int, q: int, t: float) -> float:
        # Com timeout adaptativo, o valor vigente é o do último evento
        last = last_view(p, q, t)
        return last["timeout_ms"] / 1000 if last and "timeout_ms" in last else base_timeout(p)

    # Completude e permanência
    crashes = [
        (r["target"], r["ts"]) for r in records
        if r["event"] == "fault" and r["action"] in CRASH_ACTIONS
    ]
    for q, t_crash in crashes:
        t_back = timeline.next_up(q, t_crash)
        for p in nodes:
            if p == q:
                continue
            bound = timeout_at(p, q, t_crash) + slack
            # Só cobra de quem esteve no ar durante toda a janela, e só se a
            # vítima ficou fora o suficiente para ser detectada
            if t_back - t_crash < bound or timeline.down_during(p, t_crash, t_crash + bound):
                continue
            entry = {"target": q, "observer": p, "crash_ts": t_crash, "bound_s": round(bound, 3)}
            last = last_view(p, q, t_crash)
            if last and last["event"] == "suspect":
                detected_ts = t_crash  # já suspeitava antes do crash
            else:
                hits = [r for r in views[(p, q)] if r["event"] == "suspect" and t_crash < r["ts"] <= t_crash + bound]
                if not hits:
                    report.completeness_violations.append(entry)
                    continue
                detected_ts = hits[0]["ts"]
            report.detections.append({**entry, "latency_s": round(detected_ts - t_crash, 6)})
            restored = [r for r in views[(p, q)] if r["event"] == "restore" and detected_ts < r["ts"] < t_back]
            if restored:
                report.permanence_violations.append({**entry, "restore_ts": restored[0]["ts"]})

    # Precisão
    for (p, q), events in views.items():
        for r in events:
            if r["event"] != "suspect":
                continue
            window = r.get("timeout_ms", base_timeout(p) * 1000) / 1000 + slack
            if timeline.down_during(q, r["ts"] - window, r["ts"]) or timeline.down_during(p, r["ts"] - window, r["ts"]):
                report.justified_suspicions += 1
            else:
                report.false_suspicions.append({"observer": p, "suspect": q, "ts": r["ts"]})

    # Recuperação: reinício (todo node_start exceto o primeiro de cada nó) ou unpause
    seen: set[int] = set()
    returns = []
    for r in starts:
        if r["node"] in seen:
            returns.append((r["node"], r["ts"]))
        seen.add(r["node"])
    returns += [(r["target"], r["ts"]) for r in records if r["event"] == "fault" and r["action"] == "unpause"]
    for q, t_back in returns:
        for p in nodes:
            if p == q or timeline.down_during(p, t_back, t_back + recovery_bound):
                continue
            last = last_view(p, q, t_back)
            if not (last and last["event"] == "suspect"):
                continue  # p nem chegou a suspeitar (queda curta)
            entry = {"target": q, "observer": p, "back_ts": t_back}
            hits = [r for r in views[(p, q)] if r["event"] == "restore" and t_back < r["ts"] <= t_back + recovery_bound]
            if hits:
                report.recoveries.append({**entry, "latency_s": round(hits[0]["ts"] - t_back, 6)})
            else:
                report.recovery_violations.append(entry)

    return report


# --- saída -----------------------------------------------------------------

def format_report(report: Report) -> str:
    lines = [
        f"nós: {report.nodes}   duração observada: {report.duration_s:.1f} s"
        + (f"   linhas inválidas: {report.garbage_lines}" if report.garbage_lines else ""),
    ]
    lat = sorted(d["latency_s"] for d in report.detections)
    if lat:
        lines.append(
            f"completude: {len(lat)} detecções, latência mín/mediana/máx = "
            f"{lat[0]:.3f}/{lat[len(lat) // 2]:.3f}/{lat[-1]:.3f} s"
        )
    else:
        lines.append("completude: nenhum crash a verificar")
    for v in report.completeness_violations:
        lines.append(f"  VIOLAÇÃO: nó {v['observer']} não suspeitou do nó {v['target']} em {v['bound_s']} s")
    for v in report.permanence_violations:
        lines.append(f"  VIOLAÇÃO: nó {v['observer']} restaurou o nó {v['target']} enquanto ele estava fora")
    lines.append(
        f"precisão: {len(report.false_suspicions)} falsas suspeitas "
        f"({report.false_suspicion_rate:.3f} por par·min), {report.justified_suspicions} justificadas"
    )
    for f in report.false_suspicions[:10]:
        lines.append(f"  falsa: nó {f['observer']} suspeitou do nó {f['suspect']} em ts={f['ts']:.3f}")
    if report.recoveries or report.recovery_violations:
        rec = sorted(r["latency_s"] for r in report.recoveries)
        lines.append(f"recuperação: {len(rec)} restores" + (f", latência máx = {rec[-1]:.3f} s" if rec else ""))
    for v in report.recovery_violations:
        lines.append(f"  VIOLAÇÃO: nó {v['observer']} não restaurou o nó {v['target']}")
    lines.append("OK" if report.ok else "FALHOU")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", help="logs JSONL dos nós e registros de falhas")
    parser.add_argument("--slack", type=float, default=0.5, help="folga (s) somada ao timeout (padrão 0.5)")
    parser.add_argument("--recovery-bound", type=float, default=2.0, help="prazo (s) para o restore (padrão 2.0)")
    parser.add_argument("--strict-accuracy", action="store_true", help="falha se houver falsa suspeita")
    parser.add_argument("--json", help="grava o relatório completo em JSON")
    args = parser.parse_args(argv)

    records, garbage = load(args.files)
    report = check(records, args.slack, args.recovery_bound, args.strict_accuracy, garbage)
    print(format_report(report))
    if args.json:
        Path(args.json).write_text(json.dumps(report.to_dict(), indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
