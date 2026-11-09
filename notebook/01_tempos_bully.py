# %% [markdown]
# # Tempo de eleição do Bully por tamanho de rede
# Execute as células em ordem. Os dados vêm de runs.csv, uma linha por ensaio.
# N inclui o líder que caiu; após o crash, restam N-1 nós.
#
# Eleição: primeiro election_start até acordo dos sobreviventes.
# Failover: disparo do comando Docker até acordo; inclui detecção e o custo
# de disparar a falha. Todas as medidas dos gráficos estão em milissegundos.

# %%
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

# Funciona como script e em uma sessão interativa iniciada dentro do projeto.
origin = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
ROOT = next(path for path in (origin, *origin.parents) if (path / "pyproject.toml").is_file())
CSV_PATH = ROOT / "experiments/results/bully-election/runs.csv"
OUTPUT_DIR = ROOT / "experiments/results/bully-election/notebook"
SAVE_FIGURES = True

plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})

# %% [markdown]
# ## Leitura e verificação das execuções
# Execuções inválidas permanecem no DataFrame original, mas ficam fora dos
# gráficos. Não misture parâmetros diferentes na mesma comparação de N.

# %%
df = pd.read_csv(CSV_PATH).sort_values(["N", "seed"])
latencies = ["election_latency_s", "failover_latency_s"]
valid = df.loc[
    df["violations"].eq(0)
    & df["initial_agreement"].eq(1)
    & df["converged"].eq(1)
].dropna(subset=latencies).copy()
if valid.empty:
    raise ValueError("O CSV não contém ensaios válidos para os gráficos.")
parameters = [
    "HB_INTERVAL_MS", "HB_TIMEOUT_MS", "HB_ADAPTIVE", "HB_TIMEOUT_STEP_MS",
    "HB_TIMEOUT_MAX_MS", "ELECTION_TIMEOUT_MS", "COORD_TIMEOUT_MS",
    "DROP_RATE", "DELAY_MS", "JITTER_MS",
]
if df[parameters].drop_duplicates().shape[0] != 1:
    raise ValueError("Selecione uma configuração fixa de timeouts e rede antes de comparar N.")
print(f"Ensaios lidos: {len(df)}; usados: {len(valid)}; excluídos: {len(df) - len(valid)}")
print(valid.groupby("N")["seed"].count().rename("repetições").to_string())

# %% [markdown]
# ## Estatísticas por número de nós
# O desvio-padrão usa ddof=1 (amostral). Os percentis usam interpolação linear.
# Com uma única repetição, o desvio é indefinido: não há barra de variabilidade.

# %%
summary = valid.groupby("N")[latencies].agg(
    ["count", "mean", "std", "median", lambda s: s.quantile(0.95), lambda s: s.quantile(0.99)]
)
summary = summary.rename(columns={"<lambda_0>": "p95", "<lambda_1>": "p99"}, level=1)
print(summary.to_string())

# %% [markdown]
# ## Média e variabilidade
# As barras representam ±1 desvio-padrão entre repetições, não um intervalo
# de confiança. A linha pontilhada indica o timeout de resposta configurado.
# Cada painel tem seu próprio eixo Y, iniciado em zero.

# %%
fig, axes = plt.subplots(1, 2, figsize=(11, 4), layout="constrained")
for ax, metric, title, color in zip(
    axes,
    latencies,
    ["Eleição até acordo", "Comando de falha até acordo"],
    ["#2a78d6", "#eb6834"],
):
    stats = summary[metric]
    ax.errorbar(
        stats.index, stats["mean"] * 1000,
        yerr=stats["std"].fillna(0) * 1000,
        fmt="o-", capsize=4, color=color, label="Média ±1 desvio-padrão",
    )
    if metric == "election_latency_s":
        ax.axhline(valid["ELECTION_TIMEOUT_MS"].iloc[0], color="#666666",
                   linestyle="--", linewidth=1, label="Timeout de resposta")
    ax.set(title=title, xlabel="Número inicial de nós (N)", ylabel="Tempo (ms)")
    ax.set_xticks(stats.index)
    ax.set_ylim(bottom=0)
    ax.grid(axis="y", alpha=0.25)
    ax.legend(fontsize=8)

if SAVE_FIGURES:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for extension in ("png", "pdf"):
        fig.savefig(OUTPUT_DIR / f"tempos_medios.{extension}", dpi=300, bbox_inches="tight")
plt.show()

# %% [markdown]
# ## Interpretação
# Compare a escala dos dois painéis: o failover inclui o intervalo anterior
# ao início da eleição. Uma eleição próxima de ELECTION_TIMEOUT_MS sugere
# predominância da espera por ANSWER, mesmo sem aumento visível com N.
# As conclusões se limitam à configuração e à máquina usadas no experimento.

# %%
