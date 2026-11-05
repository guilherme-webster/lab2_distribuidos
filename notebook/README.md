# Notebooks em Python

Os arquivos usam o formato de células `# %%` (código) e `# %% [markdown]`
(explicações), compatível com a janela interativa do VS Code e com Jupytext.

- `01_tempos_bully.py`: leitura, validação, estatísticas e médias com desvio-padrão.
- `02_distribuicoes_bully.py`: boxplots, pontos de cada execução e percentis.
- `03_timeouts_bully.py`: médias e distribuições ao variar os timeouts com N=5.

Abra o repositório no VS Code com as extensões Python e Jupyter. Selecione
`.venv/bin/python` como ambiente/kernel e use **Run Cell** sobre cada célula,
ou **Run All**. Cada arquivo é independente; execute suas células em ordem.
Para a janela interativa, instale o kernel caso ainda esteja ausente:

```bash
.venv/bin/python -m pip install ipykernel
```

Pandas e Matplotlib já fazem parte de `make install`. Também é possível
executar os arquivos diretamente, a partir da raiz do repositório:

```bash
.venv/bin/python notebook/01_tempos_bully.py
.venv/bin/python notebook/02_distribuicoes_bully.py
```

Os arquivos leem `experiments/results/bully-election/runs.csv`. Não precisam
de Docker e não repetem os experimentos. Em sessões sem `__file__`, inicie
o kernel na raiz do repositório ou em uma subpasta dele.

As figuras são mostradas na saída das células e exportadas em PNG e PDF
para `experiments/results/bully-election/notebook/`. Para apenas visualizar,
mude `SAVE_FIGURES = False` na primeira célula de configuração.

Para executar sem interface gráfica:

```bash
MPLBACKEND=Agg .venv/bin/python notebook/01_tempos_bully.py
MPLBACKEND=Agg .venv/bin/python notebook/02_distribuicoes_bully.py
```

O notebook 03 lê `runs.csv` dos experimentos `bully-timeout-election` e
`bully-timeout-heartbeat`. As figuras vão para
`experiments/results/bully-timeouts/notebook/`. Execute depois da coleta:

```bash
.venv/bin/python notebook/03_timeouts_bully.py
```
