"""Métricas de eleição após um único crash do maior ID, sem recuperação."""

import math


def bully_metrics(records: list[dict], meta: dict) -> tuple[dict, dict]:
    """Uma medição por ensaio; exige acordo inicial e final dos sobreviventes.

    O início da falha é issued_ts: instante anterior ao comando Docker, não
    uma observação exata do crash. A convergência é o início do último
    período de acordo que persiste até o fim da janela observada.
    """
    n = meta["n"]
    start, end = meta["measure_start_ts"], meta["measure_end_ts"]
    records = sorted((r for r in records if r["ts"] <= end), key=lambda r: r["ts"])
    faults = [r for r in records if r["event"] == "fault" and start <= r.get("issued_ts", r["ts"]) <= end]
    result = {
        "initial_agreement": 0,
        "converged": 0,
        "handler_errors": sum(r["event"] == "handler_error" for r in records),
        "violations": 0,
        "fault_issued_ts": math.nan,
        "fault_completed_ts": math.nan,
        "election_started_ts": math.nan,
        "agreement_ts": math.nan,
        "failover_latency_s": math.nan,
        "election_latency_s": math.nan,
        "detection_to_election_s": math.nan,
    }
    if len(faults) != 1 or faults[0]["action"] != "kill" or faults[0]["target"] != n:
        result["violations"] = 1
        return result, {}
    fault = faults[0]
    issued = fault.get("issued_ts", fault["ts"])
    result.update(fault_issued_ts=issued, fault_completed_ts=fault["ts"])
    leaders = {}
    for record in records:
        if record["ts"] >= issued:
            break
        if record["event"] == "node_start":
            leaders.pop(record["node"], None)
        elif record["event"] == "leader_change":
            leaders[record["node"]] = record["leader"]
    result["initial_agreement"] = int(all(leaders.get(node) == n for node in range(1, n + 1)))

    survivors = set(range(1, n))
    agreement = None
    elections = []
    survivor_restart = False
    for record in records:
        if record["ts"] < issued or record.get("node") not in survivors:
            continue
        event, node = record["event"], record["node"]
        if event in ("node_start", "node_stop"):
            survivor_restart = True
            leaders.pop(node, None)
        elif event == "leader_change":
            leaders[node] = record["leader"]
        elif event == "election_start":
            elections.append(record["ts"])
        if all(leaders.get(peer) == n - 1 for peer in survivors):
            if agreement is None:
                agreement = record["ts"]
        else:
            agreement = None

    result["converged"] = int(agreement is not None)
    result["violations"] = int(
        not result["initial_agreement"] or agreement is None
        or not elections or survivor_restart or result["handler_errors"] > 0
    )
    if not result["violations"]:
        election_start = min(elections)
        if election_start > agreement:
            result["violations"] = 1
        else:
            result.update(
                election_started_ts=election_start,
                agreement_ts=agreement,
                failover_latency_s=agreement - issued,
                election_latency_s=agreement - election_start,
                detection_to_election_s=election_start - issued,
            )
    return result, {}
