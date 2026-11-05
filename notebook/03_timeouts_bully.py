# %% [markdown]
# # Efeito dos timeouts nos tempos do Bully
# Experimento exploratório: N=5 e três repetições por configuração.
# Variamos um timeout por vez, mantendo os demais parâmetros fixos.
# COORD_TIMEOUT_MS=3000 evita expirar antes do candidato neste cenário;
# ele não é uma variável deste experimento. Não há perda ou atraso artificial.

# %%
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

origin = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
ROOT = next(path for path in (origin, *origin.parents) if (path / "pyproject.toml").is_file())
RESULTS = ROOT / "experiments/results"
OUTPUT_DIR = RESULTS / "bully-timeouts/notebook"
SAVE_FIGURES = True
EXPERIMENTS = {
    "bully-timeout-election": ("ELECTION_TIMEOUT_MS", "Timeout de resposta da eleição (ms)"),
    "bully-timeout-heartbeat": ("HB_TIMEOUT_MS", "Timeout do heartbeat (ms)"),
}
METRICS = {
    "election_latency_s": "Eleição até acordo",
    "failover_latency_s": "Comando de falha até acordo",
}
COLORS = ["#2a78d6", "#eb6834"]
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})

# %% [markdown]
# ## Leitura e validação
# Os CSVs são independentes do experimento anterior de variação de N.
# Cada ensaio inválido é contado e excluído dos gráficos. As medidas
# originais estão em segundos; os gráficos usam milissegundos.

# %%
data = {}
for experiment, (parameter, _) in EXPERIMENTS.items():
    df = pd.read_csv(RESULTS / experiment / "runs.csv").sort_values([parameter, "seed"])
    fixed = [
        "N", "HB_INTERVAL_MS", "HB_TIMEOUT_MS", "HB_ADAPTIVE", "HB_TIMEOUT_STEP_MS",
        "HB_TIMEOUT_MAX_MS", "ELECTION_TIMEOUT_MS", "COORD_TIMEOUT_MS",
        "DROP_RATE", "DELAY_MS", "JITTER_MS",
    ]
    fixed.remove(parameter)
    if df[fixed].drop_duplicates().shape[0] != 1:
        raise ValueError(f"{experiment}: há outros parâmetros variando além de {parameter}.")
    valid = df.loc[
        df["violations"].eq(0) & df["initial_agreement"].eq(1) & df["converged"].eq(1)
    ].dropna(subset=list(METRICS)).copy()
    if valid.empty:
        raise ValueError(f"{experiment}: nenhum ensaio válido.")
    data[experiment] = valid
    print(f"\n{experiment}: {len(valid)} válidos, {len(df) - len(valid)} excluídos")
    print(valid.groupby(parameter)[[*METRICS, "detection_to_election_s"]]
          .agg(["count", "mean", "std", "median"]).to_string())

# %% [markdown]
# ## Médias e desvio-padrão
# Uma linha por timeout variado; uma coluna por medida. Barras: ±1 desvio
# amostral, não intervalo de confiança. A diagonal de referência representa
# apenas a espera configurada por ANSWER; não modela o custo total do failover.

# %%
fig, axes = plt.subplots(2, 2, figsize=(11, 8), layout="constrained")
for row, (experiment, (parameter, xlabel)) in enumerate(EXPERIMENTS.items()):
    valid = data[experiment]
    for column, ((metric, title), color) in enumerate(zip(METRICS.items(), COLORS)):
        ax = axes[row, column]
        stats = valid.groupby(parameter)[metric].agg(["mean", "std"])
        ax.errorbar(stats.index, stats["mean"] * 1000,
                    yerr=stats["std"].fillna(0) * 1000, fmt="o-", capsize=4,
                    color=color, label="Média ±1 desvio-padrão")
        if metric == "election_latency_s":
            reference = stats.index if parameter == "ELECTION_TIMEOUT_MS" else [
                valid["ELECTION_TIMEOUT_MS"].iloc[0]
            ] * len(stats)
            ax.plot(stats.index, reference, "--", color="#666666", label="Timeout de resposta")
        upper = (stats["mean"] + stats["std"].fillna(0)).max() * 1000
        ax.set(title=title, xlabel=xlabel, ylabel="Tempo (ms)", ylim=(0, upper * 1.12))
        ax.set_xticks(stats.index)
        ax.grid(axis="y", alpha=0.25)
        ax.legend(fontsize=8)
if SAVE_FIGURES:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for extension in ("png", "pdf"):
        fig.savefig(OUTPUT_DIR / f"medias_timeouts.{extension}", dpi=300, bbox_inches="tight")
plt.show()

# %% [markdown]
# ## Distribuições e execuções individuais
# Caixas: quartis e mediana. Bigodes: até 1,5 IQR. Cada ponto representa
# uma execução, com deslocamento horizontal determinístico para legibilidade.
# Os eixos Y estão ampliados; não começam necessariamente em zero.

# %%
fig, axes = plt.subplots(2, 2, figsize=(11, 8), layout="constrained")
for row, (experiment, (parameter, xlabel)) in enumerate(EXPERIMENTS.items()):
    valid = data[experiment]
    levels = sorted(valid[parameter].unique())
    for column, ((metric, title), color) in enumerate(zip(METRICS.items(), COLORS)):
        ax = axes[row, column]
        groups = [valid.loc[valid[parameter].eq(level), metric] * 1000 for level in levels]
        boxes = ax.boxplot(groups, positions=list(range(len(levels))), widths=0.5,
                           patch_artist=True, showfliers=False,
                           medianprops={"color": "#222222"})
        for box in boxes["boxes"]:
            box.set(facecolor=color, alpha=0.2)
        for position, values in enumerate(groups):
            offsets = [0.0] if len(values) == 1 else [
                -0.15 + 0.3 * index / (len(values) - 1) for index in range(len(values))
            ]
            ax.scatter([position + offset for offset in offsets], values,
                       color=color, s=25, zorder=3)
        ax.set_xticks(range(len(levels)), labels=[str(level) for level in levels])
        ax.set(title=title, xlabel=xlabel, ylabel="Tempo (ms), escala ampliada")
        ax.grid(axis="y", alpha=0.25)
if SAVE_FIGURES:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for extension in ("png", "pdf"):
        fig.savefig(OUTPUT_DIR / f"distribuicoes_timeouts.{extension}", dpi=300, bbox_inches="tight")
plt.show()

# %% [markdown]
# ## Interpretação
# Aumentar ELECTION_TIMEOUT_MS deve afetar a eleição e o failover. Aumentar
# HB_TIMEOUT_MS deve afetar sobretudo a etapa anterior ao início da eleição.
# O comando de falha não informa o instante exato do crash: seu custo e a
# fase dos heartbeats acrescentam variação. Três repetições por configuração
# servem para exploração; não sustentam estimativas robustas de p95/p99.
