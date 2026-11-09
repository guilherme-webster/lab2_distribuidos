"""Tentativas locais de eleição sem falhas reais, sob perda de mensagens."""

import math
from collections import Counter


REASONS = (
    "leader_suspected", "election_received", "peer_restored",
    "coordinator_timeout", "lower_coordinator", "coordinator_suspected",
)


def bully_loss_metrics(records: list[dict], meta: dict) -> tuple[dict, dict]:
    """Conta election_start por nó/tentativa, sem agrupar eleições concorrentes.

    A janela é [início, fim). Startup não conta; uma tentativa que se
    propagou ou foi repetida após o aquecimento conta normalmente.
    Falhas reais, reinícios ou ausência de nós invalidam a medição.
    """
    start, end = meta["measure_start_ts"], meta["measure_end_ts"]
    nodes = set(range(1, meta["n"] + 1))
    ordered = sorted(records, key=lambda r: r["ts"])
    window = [r for r in ordered if start <= r["ts"] < end]
    booted = {r.get("node") for r in ordered if r["event"] == "node_start" and r["ts"] < start}
    active = {r.get("node") for r in window if r["event"] == "msg_send"}
    lifecycle = sum(r["event"] in ("node_start", "node_stop") for r in window)
    errors = sum(r["event"] == "handler_error" for r in window)
    faults = sum(r["event"] == "fault" for r in ordered)
    startups = sum(r["event"] == "election_start" and r.get("reason") == "startup" for r in window)
    violations = int(
        end <= start or booted != nodes or active != nodes or lifecycle > 0
        or errors > 0 or faults > 0 or bool(meta.get("faults_planned")) or startups > 0
    )
    # A identidade inclui o nó: IDs de tentativas não são épocas globais.
    attempts = {
        (r["node"], r["election_id"]): r for r in window
        if r["event"] == "election_start" and r.get("reason") != "startup"
    }
    reasons = Counter(r["reason"] for r in attempts.values())
    suspects = sum(r["event"] == "suspect" for r in window)
    sends = sum(r["event"] == "msg_send" for r in window)
    losses = sum(r["event"] == "msg_drop" and r.get("reason") == "loss" for r in window)

    def agreement(until):
        leaders = {}
        for r in ordered:
            if r["ts"] >= until:
                break
            if r["event"] == "node_start":
                leaders.pop(r["node"], None)
            elif r["event"] == "leader_change":
                leaders[r["node"]] = r["leader"]
        return int(all(leaders.get(node) == meta["n"] for node in nodes))

    total = len(attempts) if not violations else math.nan
    result = {
        "spurious_elections": total,
        "spurious_election_rate": total * 60 / (end - start) if end > start else math.nan,
        **{f"elections_{reason}": reasons[reason] if not violations else math.nan for reason in REASONS},
        "elections_other": sum(v for k, v in reasons.items() if k not in REASONS) if not violations else math.nan,
        "false_suspicions": suspects,
        "initial_agreement": agreement(start), "final_agreement": agreement(end),
        "observed_drop_rate": losses / sends if sends else math.nan,
        "observation_s": end - start, "handler_errors": errors,
        "lifecycle_events": lifecycle, "violations": violations,
    }
    return result, {}
