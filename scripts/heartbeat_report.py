#!/usr/bin/env python3
"""Consolida três medições de heartbeat e produz uma figura para o relatório.

Reaproveita os heartbeats dos ensaios sem crashes de bully-loss para medir
falsas suspeitas. Não confunde tentativas de eleição com suspeitas do detector.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze

ROOT = Path(__file__).resolve().parent.parent


def loss_results(source: Path) -> tuple[list[dict], list[dict]]:
    """Mede falsas suspeitas por minuto do sistema e por par dirigido."""
    rows = []
    runs = analyze.load_runs(source)
    assert len(runs) == 40, "esperados 40 ensaios completos de perda"
    for run in runs:
        meta, records = run["meta"], run["records"]
        assert not meta["faults_planned"]
        assert not any(r["event"] == "fault" for r in records)
        values, _ = analyze.heartbeat_metrics(records, meta)
        assert not values["violations"]
        start, end = meta["measure_start_ts"], meta["measure_end_ts"]
        window = [r for r in records if start <= r["ts"] < end]
        assert not any(r["event"] in ("node_start", "node_stop", "handler_error") for r in window)
        assert {r["node"] for r in window if r["event"] == "msg_send"} == set(range(1, meta["n"] + 1))
        # Sem falhas, cada suspect dentro da janela deve ser falso positivo.
        assert values["false_suspicions"] == sum(r["event"] == "suspect" for r in window)
        rows.append({
            "DROP_RATE": meta["params"]["DROP_RATE"], "seed": meta["seed"],
            "N": meta["n"], "observation_s": end - start,
            "false_suspicions": values["false_suspicions"],
            "false_positives_per_system_min": values["false_suspicions"] * 60 / (end - start),
            "false_positives_per_pair_min": values["false_suspicion_rate"],
            "source": str((source / meta["config"] / f"seed-{meta['seed']:02d}").relative_to(ROOT)),
        })
    summary = []
    for rate in (0, 0.05, 0.1, 0.2):
        group = [r for r in rows if r["DROP_RATE"] == rate]
        assert len(group) == 10
        for metric in ("false_suspicions", "false_positives_per_system_min", "false_positives_per_pair_min"):
            summary.append({"DROP_RATE": rate, "metric": metric, **analyze.describe([r[metric] for r in group])})
    return rows, summary


def select(summary, metric, key):
    return sorted([r for r in summary if r["metric"] == metric], key=lambda r: r[key])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loss-only", action="store_true")
    args = parser.parse_args()
    dest = ROOT / "experiments/results/heartbeat-report"
    dest.mkdir(parents=True, exist_ok=True)
    loss_runs, loss = loss_results(ROOT / "experiments/raw/bully-loss")
    analyze.write_csv(dest / "loss-runs.csv", loss_runs)
    analyze.write_csv(dest / "loss-summary.csv", loss)
    (dest / "provenance.json").write_text(json.dumps({
        "timeout": "experiments/raw/hb-timeout", "scale": "experiments/raw/hb-scale",
        "loss": "experiments/raw/bully-loss",
        "loss_reused": True,
        "loss_measurement": "suspect, não election_start; janela após aquecimento",
        "system_rate": "false_suspicions * 60 / observation_s",
        "pair_rate": "system_rate / (N * (N - 1))",
    }, indent=2, ensure_ascii=False) + "\n")
    if args.loss_only:
        print("Perda: 40 ensaios reanalisados e validados.")
        return
    _, timeout_runs, timeout_samples, timeout = analyze.analyze(ROOT / "experiments/raw/hb-timeout")
    _, scale_runs, _, scale = analyze.analyze(ROOT / "experiments/raw/hb-scale")
    assert len(timeout_runs) == 30 and len(scale_runs) == 40
    assert not any(r["violations"] for r in timeout_runs + scale_runs)
    assert sum(r["metric"] == "recovery_latency_s" for r in timeout_samples) == 240
    quantiles = select(timeout, "detection_latency_s", "HB_TIMEOUT_MS")
    scale_stats = select(scale, "heartbeat_rate", "N")
    assert len(quantiles) == 3 and all(r["n"] == 80 for r in quantiles)
    assert len(scale_stats) == 4 and all(r["n"] == 10 for r in scale_stats)
    analyze.write_csv(dest / "timeout-summary.csv", quantiles)
    analyze.write_csv(dest / "scale-summary.csv", scale_stats)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 7.5, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(1, 2, figsize=(3.4, 1.75), layout="constrained")
    ax = axes[0]
    loss_stats = select(loss, "false_positives_per_system_min", "DROP_RATE")
    ax.errorbar([100 * r["DROP_RATE"] for r in loss_stats], [r["mean"] for r in loss_stats],
                yerr=[r["std"] for r in loss_stats], fmt="o-", capsize=2, markersize=3)
    ax.set(title="(a) Precisão, N=5", xlabel="Perda (%)", ylabel="FP / min (sistema)", xticks=[0, 5, 10, 20])
    ax = axes[1]
    ns = [r["N"] for r in scale_stats]
    ax.errorbar(ns, [r["mean"] for r in scale_stats], yerr=[r["std"] for r in scale_stats], fmt="o-", capsize=2, label="Medido", markersize=3)
    ax.plot(ns, [n * (n - 1) * 5 for n in ns], "k--", label="Teórico", linewidth=0.8)
    ax.set(title="(b) Custo all-to-all", xlabel="Nós (N)", ylabel="Heartbeats / s", xticks=ns)
    ax.legend(fontsize=6.5)
    for ax in axes:
        ax.set_ylim(bottom=0)
        ax.grid(axis="y", alpha=0.25)
    for ext in ("pdf", "png"):
        fig.savefig(ROOT / "images" / f"heartbeat-resultados.{ext}", dpi=220, bbox_inches="tight")
    plt.close(fig)
    (dest / "README.md").write_text(
        "# Resultados de heartbeat\n\n"
        "Reprodução da consolidação: `.venv/bin/python scripts/heartbeat_report.py`.\n"
        "Novas execuções: `make experiments EXP=hb-timeout` e "
        "`make experiments EXP=hb-scale`.\n\n"
        "- Timeout: 30 ensaios novos, dez sementes por T=400/600/1000 ms; "
        "intervalo 200 ms, N=5, detector fixo, sem perdas. Cada ensaio tem "
        "5 s de aquecimento e 28 s de observação, duas quedas com retorno. "
        "São 80 detecções por timeout, provenientes de dez execuções.\n"
        "- Perda: reanálise dos heartbeats dos 40 ensaios Docker de bully-loss, "
        "dez sementes por perda, N=5, timeout 600 ms, intervalo 200 ms, "
        "5 s de aquecimento e 30 s de observação sem crashes. Contam-se "
        "suspeitas, não eleições. A taxa do sistema é o total de suspeitas "
        "vezes 60 dividido pela duração; a taxa por par divide ainda por 20.\n"
        "- Escala: 40 ensaios novos, dez sementes por N=3/5/7/9, sem perdas "
        "ou crashes; 5 s de aquecimento e 12 s de medição. Somente "
        "HEARTBEAT entra na contagem de mensagens/s.\n\n"
        "A latência é estimada desde o último sinal de vida da vítima durante "
        "o comando Docker, não desde um instante exato de crash. Os quatro "
        "observadores da mesma queda não são repetições independentes. "
        "Os percentis usam interpolação linear; DP é amostral.\n\n"
        "Os CSVs consolidados e provenance.json identificam as fontes. "
        "Os logs originais ficam em experiments/raw/. A figura está em "
        "../../../images/heartbeat-resultados.pdf e .png.\n",
    )
    print("30 ensaios de timeout, 40 de perda reutilizados e 40 de escala validados.")
    print(json.dumps({"timeout": quantiles, "loss": loss_stats, "scale": scale_stats}, indent=2))


if __name__ == "__main__":
    main()
