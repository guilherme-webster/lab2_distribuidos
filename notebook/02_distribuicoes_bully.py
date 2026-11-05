# %% [markdown]
# # Distribuição dos tempos do Bully
# Cada ponto representa uma execução. As caixas mostram os quartis; a linha
# interna é a mediana. Os bigodes vão até 1,5 vezes o intervalo interquartil.
# Os pontos incluem todas as observações, inclusive valores além dos bigodes.

# %%
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

origin = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
ROOT = next(path for path in (origin, *origin.parents) if (path / "pyproject.toml").is_file())
CSV_PATH = ROOT / "experiments/results/bully-election/runs.csv"
OUTPUT_DIR = ROOT / "experiments/results/bully-election/notebook"
SAVE_FIGURES = True

# %%
df = pd.read_csv(CSV_PATH).sort_values(["N", "seed"])
metrics = ["election_latency_s", "failover_latency_s"]
valid = df.loc[
    df["violations"].eq(0)
    & df["initial_agreement"].eq(1)
    & df["converged"].eq(1)
].dropna(subset=metrics).copy()
if valid.empty:
    raise ValueError("O CSV não contém ensaios válidos para os gráficos.")
parameters = [
    "HB_INTERVAL_MS", "HB_TIMEOUT_MS", "HB_ADAPTIVE", "HB_TIMEOUT_STEP_MS",
    "HB_TIMEOUT_MAX_MS", "ELECTION_TIMEOUT_MS", "COORD_TIMEOUT_MS",
    "DROP_RATE", "DELAY_MS", "JITTER_MS",
]
if df[parameters].drop_duplicates().shape[0] != 1:
    raise ValueError("Selecione uma configuração fixa de timeouts e rede antes de comparar N.")
print(f"Ensaios usados: {len(valid)}; excluídos: {len(df) - len(valid)}")
print(valid.groupby("N")[metrics].quantile([0.5, 0.95, 0.99]).to_string())

# %% [markdown]
# ## Distribuição por N e pontos individuais
# O eixo Y está ampliado para revelar diferenças pequenas. Diferentemente
# do gráfico de médias, ele não começa necessariamente em zero.
# O deslocamento horizontal dos pontos é fixo, apenas para evitar sobreposição.

# %%
sizes = sorted(valid["N"].unique())
fig, axes = plt.subplots(1, 2, figsize=(11, 4), layout="constrained")
for ax, metric, title, color in zip(
    axes, metrics,
    ["Distribuição do tempo de eleição", "Distribuição do tempo de failover"],
    ["#2a78d6", "#eb6834"],
):
    groups = [valid.loc[valid["N"].eq(n), metric] * 1000 for n in sizes]
    boxes = ax.boxplot(groups, positions=list(range(len(sizes))), widths=0.5,
                       patch_artist=True, showfliers=False,
                       medianprops={"color": "#222222", "linewidth": 1.5})
    for box in boxes["boxes"]:
        box.set(facecolor=color, alpha=0.2)
    for position, values in enumerate(groups):
        offsets = [0.0] if len(values) == 1 else [
            -0.15 + 0.3 * index / (len(values) - 1) for index in range(len(values))
        ]
        ax.scatter([position + offset for offset in offsets], values,
                   s=22, color=color, alpha=0.8, zorder=3)
    ax.set_xticks(range(len(sizes)), labels=[str(n) for n in sizes])
    ax.set(title=title, xlabel="Número inicial de nós (N)", ylabel="Tempo (ms), escala ampliada")
    ax.grid(axis="y", alpha=0.25)
    ax.spines[["top", "right"]].set_visible(False)

if SAVE_FIGURES:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for extension in ("png", "pdf"):
        fig.savefig(OUTPUT_DIR / f"distribuicoes.{extension}", dpi=300, bbox_inches="tight")
plt.show()

# %% [markdown]
# Apenas dez repetições por N: os percentis altos dependem bastante dos
# maiores valores observados. Os dados são de uma única máquina, sem perda
# ou atraso artificial. N inclui o líder morto; há N-1 sobreviventes.

# %%
