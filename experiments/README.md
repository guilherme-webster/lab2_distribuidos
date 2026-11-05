# Experimento de tempo de eleição Bully

```bash
make experiments EXP=bully-election
```

Executa 10 repetições para cada N em `[3, 5, 7, 9, 11, 13]`, em um projeto
Docker isolado (`mc714-exp`). Cada ensaio espera todos os nós iniciarem,
aquece por 10 s e observa 8 s. Após 1 s de medição, mata o nó de maior ID.
Os sobreviventes devem concordar em líder N-1. Não há perda ou atraso
artificial. As seeds são intercaladas entre os tamanhos de rede; com a
rede sem perdas, a variabilidade vem sobretudo do escalonamento real.

Os parâmetros estão em `configs/bully-election.toml`. Para um piloto com
uma repetição por N, acrescente `SEEDS=1` ao comando. Ensaios completos são
reutilizados ao retomar. Para apenas recalcular os resultados:

```bash
make analyze EXP=bully-election
```

`results/bully-election/runs.csv` contém uma linha por ensaio, com N,
seed, parâmetros e as seguintes medidas em segundos:

| Coluna | Definição |
|---|---|
| `failover_latency_s` | Disparo do comando de falha até acordo dos sobreviventes |
| `election_latency_s` | Primeiro `election_start` após a falha até acordo dos sobreviventes |
| `detection_to_election_s` | Disparo da falha até primeiro `election_start` |
| `initial_agreement` | Todos reconheciam N como líder antes da falha (0/1) |
| `converged` | Todos os sobreviventes reconhecem N-1 ao fim da janela (0/1) |
| `violations` | Ensaio inválido para medir latência (0/1) |

Acordo é medido pelo início do último período em que todos os sobreviventes
reconhecem N-1, sem divergência posterior até o fim da observação. Não é
garantia de estabilidade além dessa janela. Falta de acordo inicial/final,
reinício/parada de sobrevivente ou erro em handler invalida a medição;
nesse caso, as latências ficam como NaN, sem contribuir para o resumo.

O crash ocorre entre `fault_issued_ts` e `fault_completed_ts`; o primeiro
é usado como referência reproduzível. Assim, o tempo total inclui parte
do custo de disparar o comando Docker. Os timestamps são comparáveis
porque os contêineres rodam no mesmo host.

```python
import pandas as pd

df = pd.read_csv("experiments/results/bully-election/runs.csv")
valid = df.loc[df["violations"].eq(0)]
print(valid.groupby("N")["election_latency_s"].agg(["count", "mean", "std", "median"]))
```

Também são gerados `summary.csv` (média, desvio e percentis) e gráficos.
Os logs comprimidos, falhas, metadados e informações da máquina ficam
em `raw/bully-election/`, permitindo reanálise. `N` inclui o líder que
será morto; após o crash, restam N-1 nós.

## Piloto rápido: efeito dos timeouts

```bash
make experiments EXP="bully-timeout-election bully-timeout-heartbeat"
```

São 18 ensaios: N=5, três repetições por configuração, sem perdas/atrasos
artificiais. Cada ensaio aquece por 3 s, observa 5 s e mata o líder após
0,5 s de medição. Varia-se um fator por vez:

- `bully-timeout-election`: ELECTION_TIMEOUT_MS em 200/500/1000 ms,
  HB_TIMEOUT_MS fixo em 600 ms.
- `bully-timeout-heartbeat`: HB_TIMEOUT_MS em 400/600/1200 ms,
  ELECTION_TIMEOUT_MS fixo em 500 ms.

HB_INTERVAL_MS=200 e COORD_TIMEOUT_MS=3000 são fixos. O timeout do
coordenador não é avaliado: seu efeito exige um candidato que responda e
falhe antes de anunciar o coordenador. Aqui o crash é do líder já eleito.

Cada experimento gera seus próprios `runs.csv`, `summary.csv` e gráficos
em `results/<nome>/`. `notebook/03_timeouts_bully.py` reúne as duas séries
em gráficos de médias e distribuições, no mesmo formato de células `# %%`.
Trata-se de um piloto com apenas três repetições, voltado a observar a
influência dos timeouts; percentis altos devem ser interpretados com cautela.
