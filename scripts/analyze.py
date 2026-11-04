#!/usr/bin/env python3
"""Analisa os dados brutos de um experimento: métricas, estatísticas e gráficos.

    python scripts/analyze.py experiments/raw/hb-loss [--out experiments/results]

Para cada ensaio completo (meta.json com "complete": true), calcula as
métricas do algoritmo indicado em spec.toml (`algorithm`) e grava em
<out>/<experimento>/:

    runs.csv     uma linha por ensaio (métricas escalares)
    samples.csv  uma linha por amostra (ex.: cada latência de detecção)
    summary.csv  por configuração e métrica: n, média, desvio, mín, p5, p50,
                 p95, p99, máx (amostras de todas as seeds juntas; métricas
                 escalares: um valor por seed)
    *.png/*.pdf  gráficos pedidos em [[plots]] na especificação

Métricas de latência (amostras) são desenhadas pela mediana com faixa p5–p95;
as escalares, pela média com faixa de ±1 desvio-padrão.
"""

import argparse
import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_heartbeat  # noqa: E402
import run_experiment  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATS = ["n", "mean", "std", "min", "p5", "p50", "p95", "p99", "max"]


# --- estatística -----------------------------------------------------------

def percentile(values: list[float], p: float) -> float:
    """Percentil com interpolação linear (o mesmo método padrão do numpy)."""
    if not values:
        return math.nan
    ordered = sorted(values)
    k = (len(ordered) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def describe(values: list[float]) -> dict:
    if not values:
        return {s: (0 if s == "n" else math.nan) for s in STATS}
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "std": statistics.stdev(values) if len(values) > 1 else 0.0,
        "min": min(values),
        "p5": percentile(values, 5),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values),
    }


# --- métricas por algoritmo ------------------------------------------------

def last_sign_of_life(records: list[dict], node: int, until: float) -> float | None:
    """ts do último evento registrado pelo nó até `until`."""
    last = None
    for r in records:
        if r["ts"] > until:
            break
        if r.get("node") == node and r["event"] != "fault":
            last = r["ts"]
    return last


def heartbeat_metrics(records: list[dict], meta: dict) -> tuple[dict, dict]:
    """Métricas do detector de heartbeat. Devolve (escalares, amostras)."""
    report = check_heartbeat.check(records)
    start, end = meta["measure_start_ts"], meta["measure_end_ts"]
    minutes = (end - start) / 60
    n = meta["n"]
    faults = {(r["target"], check_heartbeat.fault_ts(r)): r for r in records if r["event"] == "fault"}

    latencies, already = [], 0
    for d in report.detections:
        if d["latency_s"] == 0:
            already += 1  # já suspeitava antes do crash: latência indefinida
            continue
        suspect_ts = d["crash_ts"] + d["latency_s"]
        # O crash ocorreu entre o disparo do comando e o fim dele; o último
        # evento logado pela vítima nesse intervalo estreita a estimativa
        fault = faults.get((d["target"], d["crash_ts"]), {})
        sign = last_sign_of_life(records, d["target"], fault.get("ts", d["crash_ts"]))
        crash_ts = max(d["crash_ts"], sign) if sign is not None else d["crash_ts"]
        latencies.append(suspect_ts - crash_ts)

    false_in_window = [f for f in report.false_suspicions if start <= f["ts"] <= end]
    # Taxa de heartbeats só na fase sem falhas, comparável a N(N-1)/intervalo
    calm_end = min([check_heartbeat.fault_ts(r) for r in records if r["event"] == "fault"] + [end])
    heartbeats = sum(
        1 for r in records
        if r["event"] == "msg_send" and r.get("type") == "HEARTBEAT" and start <= r["ts"] <= calm_end
    )
    scalars = {
        "false_suspicions": len(false_in_window),
        "false_suspicion_rate": len(false_in_window) / (n * (n - 1) * minutes) if minutes else math.nan,
        "heartbeat_rate": heartbeats / (calm_end - start) if calm_end > start else math.nan,
        "detections": len(latencies),
        "detections_already_suspected": already,
        "violations": len(report.completeness_violations) + len(report.permanence_violations)
        + len(report.recovery_violations),
    }
    samples = {
        "detection_latency_s": latencies,
        "recovery_latency_s": [r["latency_s"] for r in report.recoveries],
    }
    return scalars, samples


# O Bully entra aqui com sua própria função (escalares, amostras)
METRICS = {"heartbeat": heartbeat_metrics}

# Curvas teóricas que um gráfico pode desenhar como referência
REFERENCES = {
    "heartbeat_rate": lambda p: p["N"] * (p["N"] - 1) * 1000 / p["HB_INTERVAL_MS"],
}


# --- leitura dos ensaios ---------------------------------------------------

def load_runs(exp_dir: Path) -> list[dict]:
    runs = []
    for meta_path in sorted(exp_dir.glob("*/seed-*/meta.json")):
        meta = json.loads(meta_path.read_text())
        if not meta.get("complete"):
            continue
        run_dir = meta_path.parent
        logs = run_dir / "logs.jsonl.gz"
        if not logs.exists():
            logs = run_dir / "logs.jsonl"
        records, _ = check_heartbeat.load([logs, run_dir / "faults.jsonl"])
        runs.append({"meta": meta, "records": records})
    return runs


def analyze(exp_dir: Path) -> tuple[run_experiment.Spec, list[dict], list[dict], list[dict]]:
    spec = run_experiment.load_spec(exp_dir / "spec.toml")
    algorithm = spec.raw.get("algorithm", "heartbeat")
    if algorithm not in METRICS:
        raise SystemExit(f"algoritmo sem métricas definidas em analyze.py: {algorithm!r}")
    metrics_fn = METRICS[algorithm]

    run_rows, sample_rows = [], []
    for run in load_runs(exp_dir):
        meta = run["meta"]
        scalars, samples = metrics_fn(run["records"], meta)
        base = {"config": meta["config"], **meta["params"], "seed": meta["seed"]}
        run_rows.append({**base, **scalars})
        for metric, values in samples.items():
            sample_rows += [{**base, "metric": metric, "value": v} for v in values]

    # Resumo por configuração: amostras de todas as seeds; escalares, uma por seed
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    params_of: dict[str, dict] = {}
    for row in sample_rows:
        groups[(row["config"], row["metric"])].append(row["value"])
    scalar_names = [k for k in (run_rows[0] if run_rows else {}) if k not in {"config", "seed", *spec.base, *spec.sweep}]
    for row in run_rows:
        params_of[row["config"]] = {k: row[k] for k in (*spec.base, *spec.sweep)}
        for metric in scalar_names:
            groups[(row["config"], metric)].append(row[metric])
    summary = [
        {"config": config, **params_of.get(config, {}), "metric": metric, **describe(values)}
        for (config, metric), values in sorted(groups.items())
    ]
    return spec, run_rows, sample_rows, summary


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


# --- gráficos --------------------------------------------------------------

# Paleta categórica de referência (slots 1–3, validados para daltonismo) e
# tintas do gráfico; marcadores diferentes dão identidade além da cor
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a"]
MARKERS = ["o", "s", "^"]
SURFACE, INK, INK_2, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
SAMPLE_METRICS = {"detection_latency_s", "recovery_latency_s"}


def plot(spec_plot: dict, summary: list[dict], out_base: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    metric, x = spec_plot["metric"], spec_plot["x"]
    series_key = spec_plot.get("series")
    labels = spec_plot.get("series_labels", {})
    rows = [r for r in summary if r["metric"] == metric and r["n"]]
    if not rows:
        return []
    by_series: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        key = run_experiment.env_value(r[series_key]) if series_key else ""
        by_series[key].append(r)

    sampled = metric in SAMPLE_METRICS
    center, lo_key, hi_key = ("p50", "p5", "p95") if sampled else ("mean", None, None)

    plt.rcParams.update({"font.size": 8, "font.family": "sans-serif", "axes.titlesize": 8})
    fig, ax = plt.subplots(figsize=(3.5, 2.4), dpi=300)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    for i, (key, group) in enumerate(sorted(by_series.items())):
        group.sort(key=lambda r: r[x])
        xs = [r[x] for r in group]
        ys = [r[center] for r in group]
        if sampled:
            lows, highs = [r[lo_key] for r in group], [r[hi_key] for r in group]
        else:
            lows = [r["mean"] - r["std"] for r in group]
            highs = [r["mean"] + r["std"] for r in group]
        color = SERIES_COLORS[i % len(SERIES_COLORS)]
        name = labels.get(key, key) if series_key else None
        ax.fill_between(xs, lows, highs, color=color, alpha=0.10, linewidth=0)
        ax.plot(xs, ys, color=color, linewidth=1.25, solid_capstyle="round", solid_joinstyle="round",
                marker=MARKERS[i % len(MARKERS)], markersize=5, markeredgecolor=SURFACE,
                markeredgewidth=1, label=name, zorder=3)
        if name:  # rótulo direto no último ponto, em tinta de texto
            ax.annotate(name, (xs[-1], ys[-1]), xytext=(6, 0), textcoords="offset points",
                        va="center", fontsize=7, color=INK_2)

    reference = spec_plot.get("reference")
    if reference in REFERENCES:
        xs = sorted({r[x] for r in rows})
        ref_rows = {r[x]: r for r in rows}
        ys = [REFERENCES[reference](ref_rows[v]) for v in xs]
        ax.plot(xs, ys, color=MUTED, linewidth=1, linestyle=(0, (4, 3)), label="teórico", zorder=2)

    ax.set_xlabel(spec_plot.get("xlabel", x), color=INK_2)
    ax.set_ylabel(spec_plot.get("ylabel", metric), color=INK_2)
    ax.set_xticks(sorted({r[x] for r in rows}))
    ax.grid(axis="y", color=GRID, linewidth=0.5)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelcolor=INK_2, labelsize=7)
    ax.set_ylim(bottom=0)
    band = "mediana, faixa p5–p95" if sampled else "média, faixa ±1 desvio"
    ax.set_title(band, loc="left", color=MUTED, fontsize=7)
    handles, _ = ax.get_legend_handles_labels()
    if len(handles) >= 2:
        ax.legend(frameon=False, fontsize=7, labelcolor=INK_2)
    fig.tight_layout()

    paths = []
    for ext in ("png", "pdf"):
        path = out_base.with_suffix(f".{ext}")
        fig.savefig(path, facecolor=SURFACE)
        paths.append(path)
    plt.close(fig)
    return paths


# --- linha de comando ------------------------------------------------------

def fmt(value) -> str:
    if isinstance(value, float):
        return "—" if math.isnan(value) else f"{value:.3f}"
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("exp_dir", help="diretório do experimento em experiments/raw/")
    parser.add_argument("--out", default="experiments/results", help="raiz dos resultados")
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args(argv)

    exp_dir = Path(args.exp_dir)
    spec, run_rows, sample_rows, summary = analyze(exp_dir)
    if not run_rows:
        print(f"nenhum ensaio completo em {exp_dir}", file=sys.stderr)
        return 1
    out = Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out
    out_dir = out / spec.name
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "runs.csv", run_rows)
    write_csv(out_dir / "samples.csv", sample_rows)
    write_csv(out_dir / "summary.csv", summary)

    print(f"{spec.name}: {len(run_rows)} ensaios em {len({r['config'] for r in run_rows})} configurações")
    shown = {p["metric"] for p in spec.raw.get("plots", [])} | {"violations"}
    for r in summary:
        if r["metric"] in shown:
            stats = "  ".join(f"{s}={fmt(r[s])}" for s in ("n", "mean", "p50", "p95", "p99"))
            print(f"  {r['config']:<40} {r['metric']:<22} {stats}")
    violations = sum(r["violations"] for r in run_rows)
    if violations:
        print(f"ATENÇÃO: {violations} violações de propriedade nos ensaios (ver runs.csv)")

    if not args.no_plots:
        for spec_plot in spec.raw.get("plots", []):
            name = "__".join(filter(None, (spec_plot["metric"], spec_plot["x"], spec_plot.get("series"))))
            for path in plot(spec_plot, summary, out_dir / name):
                print(f"  gráfico: {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
