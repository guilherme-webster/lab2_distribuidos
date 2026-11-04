import json

import pytest

from dsnode.messages import MsgType
from tests.scripts import load_script
from tests.test_heartbeat import TIMEOUT, cluster

chk = load_script("check_heartbeat")

NODES = [1, 2, 3]


# --- construtores de registros ---------------------------------------------

def start(node, ts=0.0, timeout_ms=600, nodes=NODES):
    peers = {str(p): [f"node{p}", 9000] for p in nodes if p != node}
    return {"ts": ts, "node": node, "event": "node_start",
            "config": {"peers": peers, "hb_interval_ms": 200, "hb_timeout_ms": timeout_ms}}


def fault(action, target, ts):
    return {"ts": ts, "event": "fault", "action": action, "target": target}


def suspect(p, q, ts, timeout_ms=600):
    return {"ts": ts, "node": p, "event": "suspect", "peer": q, "timeout_ms": timeout_ms}


def restore(p, q, ts, false_suspicion=False, timeout_ms=600):
    return {"ts": ts, "node": p, "event": "restore", "peer": q,
            "false_suspicion": false_suspicion, "timeout_ms": timeout_ms}


def tick(ts):
    """Qualquer evento, só para marcar até quando o ensaio foi observado."""
    return {"ts": ts, "node": 1, "event": "msg_send"}


def run(*records, **kwargs):
    return chk.check(sorted(records, key=lambda r: r["ts"]), **kwargs)


BOOT = [start(n) for n in NODES]


# --- operação normal --------------------------------------------------------

def test_normal_run_is_ok():
    report = run(*BOOT, tick(60.0))
    assert report.ok
    assert report.nodes == NODES and report.duration_s == 60.0
    assert report.false_suspicions == [] and report.detections == []


def test_suspicion_of_late_starter_is_justified():
    report = run(start(1), start(2), start(3, ts=2.0), suspect(1, 3, 0.6), restore(1, 3, 2.1), tick(10))
    assert report.ok and report.false_suspicions == [] and report.justified_suspicions == 1


# --- completude -------------------------------------------------------------

def test_crash_detected_by_all():
    report = run(*BOOT, fault("kill", 3, 10.0), suspect(1, 3, 10.55), suspect(2, 3, 10.6), tick(20))
    assert report.ok
    assert sorted(d["latency_s"] for d in report.detections) == pytest.approx([0.55, 0.6])
    assert report.false_suspicions == []


def test_missing_detection_is_a_violation():
    report = run(*BOOT, fault("kill", 3, 10.0), suspect(1, 3, 10.6), tick(20))
    assert not report.ok
    assert [(v["observer"], v["target"]) for v in report.completeness_violations] == [(2, 3)]


def test_late_detection_is_a_violation():
    report = run(*BOOT, fault("kill", 3, 10.0), suspect(1, 3, 10.6), suspect(2, 3, 12.0), tick(20))
    assert [v["observer"] for v in report.completeness_violations] == [2]


def test_crash_shorter_than_bound_is_not_required():
    report = run(*BOOT, fault("kill", 3, 10.0), start(3, 10.3), tick(20))
    assert report.ok and report.detections == []


def test_fault_time_is_when_the_command_was_issued():
    # docker kill disparado em 10.0 e terminado em 10.5: a suspeita em 10.45
    # vem depois do disparo, então conta como detecção (e não como prévia)
    kill = {**fault("kill", 3, 10.5), "issued_ts": 10.0, "cmd_ms": 500.0}
    report = run(*BOOT, kill, suspect(1, 3, 10.45), suspect(2, 3, 10.6), tick(20))
    assert report.ok and report.false_suspicions == []
    assert {d["observer"]: d["latency_s"] for d in report.detections} == pytest.approx({1: 0.45, 2: 0.6})
    assert all(d["uncertainty_s"] == 0.5 for d in report.detections)


def test_restore_during_unpause_command_counts_as_recovery():
    unpause = {**fault("unpause", 3, 12.1), "issued_ts": 12.0, "cmd_ms": 100.0}
    report = run(*BOOT, fault("pause", 3, 10.0), suspect(1, 3, 10.6), suspect(2, 3, 10.6),
                 unpause, restore(1, 3, 12.05, True), restore(2, 3, 12.08, True), tick(20))
    assert report.ok and len(report.recoveries) == 2


def test_already_suspected_counts_as_immediate_detection():
    report = run(*BOOT, suspect(1, 3, 9.0), fault("kill", 3, 10.0), suspect(2, 3, 10.6), tick(20))
    assert report.ok
    assert {d["observer"]: d["latency_s"] for d in report.detections} == {1: 0.0, 2: pytest.approx(0.6)}
    assert len(report.false_suspicions) == 1  # a de 9.0, com o nó 3 no ar


def test_restore_while_down_violates_permanence():
    report = run(*BOOT, fault("kill", 3, 10.0), suspect(1, 3, 10.6), suspect(2, 3, 10.6),
                 restore(1, 3, 12.0), tick(20))
    assert [v["observer"] for v in report.permanence_violations] == [1]
    assert not report.ok


def test_adaptive_timeout_widens_the_bound():
    # O nó 1 já tinha aumentado o timeout do nó 3 para 1,2 s
    records = [*BOOT, suspect(1, 3, 5.0), restore(1, 3, 5.3, True, timeout_ms=1200),
               fault("kill", 3, 10.0), suspect(1, 3, 11.4, timeout_ms=1200), suspect(2, 3, 10.6), tick(20)]
    assert run(*records).completeness_violations == []


# --- precisão ---------------------------------------------------------------

def test_false_suspicion_is_counted_but_ok_unless_strict():
    records = [*BOOT, suspect(1, 2, 30.0), restore(1, 2, 30.2, True), tick(60)]
    report = run(*records)
    assert report.ok and len(report.false_suspicions) == 1
    assert report.false_suspicion_rate == pytest.approx(1 / (6 * 1.0))  # 6 pares, 1 min
    assert not run(*records, strict_accuracy=True).ok


def test_pause_of_suspect_or_observer_justifies_suspicion():
    report = run(*BOOT, fault("pause", 3, 10.0), suspect(1, 3, 10.6), fault("unpause", 3, 12.0),
                 restore(1, 3, 12.1, True),
                 # o nó 3, ao despausar, vence seus timers e suspeita de todos
                 suspect(3, 1, 12.0), suspect(3, 2, 12.0), tick(20))
    assert report.false_suspicions == [] and report.justified_suspicions == 3


# --- recuperação ------------------------------------------------------------

def test_recovery_restore_is_measured():
    report = run(*BOOT, fault("kill", 3, 10.0), suspect(1, 3, 10.6), suspect(2, 3, 10.6),
                 start(3, 15.0), restore(1, 3, 15.2), restore(2, 3, 15.3), tick(20))
    assert report.ok
    assert sorted(r["latency_s"] for r in report.recoveries) == pytest.approx([0.2, 0.3])


def test_missing_restore_after_unpause_is_a_violation():
    report = run(*BOOT, fault("pause", 3, 10.0), suspect(1, 3, 10.6), suspect(2, 3, 10.6),
                 fault("unpause", 3, 12.0), restore(1, 3, 12.1, True), tick(20))
    assert [v["observer"] for v in report.recovery_violations] == [2]
    assert not report.ok


# --- linha de comando -------------------------------------------------------

def test_cli_reads_files_writes_json_and_sets_exit_code(tmp_path, capsys):
    logs = tmp_path / "logs.jsonl"
    faults = tmp_path / "faults.jsonl"
    logs.write_text("\n".join(json.dumps(r) for r in [*BOOT, suspect(1, 3, 10.6), tick(20)]) + "\nlixo\n")
    faults.write_text(json.dumps(fault("kill", 3, 10.0)) + "\n")
    out = tmp_path / "report.json"

    assert chk.main([str(logs), str(faults), "--json", str(out)]) == 1  # nó 2 não detectou
    assert "FALHOU" in capsys.readouterr().out
    data = json.loads(out.read_text())
    assert data["ok"] is False and data["garbage_lines"] == 1
    assert data["completeness_violations"][0]["observer"] == 2


# --- concordância com o detector real (simulador) ---------------------------

def sim_records(c, faults=()):
    records = [start(i, 0.0, timeout_ms=int(TIMEOUT * 1000), nodes=list(c.det)) for i in c.det]
    for log in c.logs.values():
        records += log.records
    return sorted([*records, *faults], key=lambda r: r["ts"])


async def test_simulated_crash_and_recovery_pass_the_checker():
    c = await cluster(5)
    await c.net.run(2.0)
    crash_at = c.clock.now()
    c.crash(4)
    await c.net.run(3.0)
    back_at = c.clock.now()
    old_log = c.logs[4]
    await c.boot(4, incarnation=1)
    await c.net.run(2.0)
    restart = start(4, back_at, timeout_ms=int(TIMEOUT * 1000), nodes=list(c.det))
    records = sim_records(c, [fault("kill", 4, crash_at), restart, *old_log.records])
    report = chk.check(records, slack=0.05, strict_accuracy=True)
    assert report.ok, chk.format_report(report)
    assert len(report.detections) == 4 and len(report.recoveries) == 4


async def test_checker_agrees_with_detector_on_false_suspicions():
    c = await cluster(4, drop_rate=0.4, seed=7)
    await c.net.run(30.0)
    slack = 0.05
    report = chk.check(sim_records(c), slack=slack)
    flagged = sum(r["false_suspicion"] for log in c.logs.values() for r in log.events("restore"))
    pending = sum(c.det[i].is_suspected(p) for i in c.det for p in c.det if p != i)
    # Ninguém caiu: toda suspeita é falsa para o detector (as ainda abertas não
    # têm restore). O verificador só aceita as do primeiro timeout, cuja janela
    # alcança o instante anterior à subida dos nós
    assert len(report.false_suspicions) + report.justified_suspicions == flagged + pending
    assert len(report.false_suspicions) > 0
    startup = [r for log in c.logs.values() for r in log.events("suspect") if r["ts"] <= TIMEOUT + slack]
    assert report.justified_suspicions == len(startup)
    assert all(m.type == MsgType.HEARTBEAT for m in c.net.dropped)


def test_cli_reads_gzipped_logs(tmp_path, capsys):
    import gzip

    logs = tmp_path / "logs.jsonl.gz"
    lines = [*BOOT, fault("kill", 3, 10.0), suspect(1, 3, 10.6), suspect(2, 3, 10.6), tick(20)]
    logs.write_bytes(gzip.compress("\n".join(json.dumps(r) for r in lines).encode()))
    assert chk.main([str(logs)]) == 0
    assert "2 detecções" in capsys.readouterr().out
