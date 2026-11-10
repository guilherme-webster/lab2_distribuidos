# MC714 — Detector de falhas e eleição de líder

Trabalho 2 de Sistemas Distribuídos. Implementação de um
detector de falhas por **heartbeat** integrado à eleição de líder **Bully**:
quando o detector suspeita do líder, uma eleição escolhe o maior ID vivo.
Cada nó executa em um contêiner independente e se comunica por UDP com
mensagens JSON. Os algoritmos usam Python e `asyncio`, sem frameworks de
detecção de falhas ou eleição.

O [planejamento](PLANEJAMENTO.md) descreve o modelo do sistema e os contratos
entre módulos. Todos os comandos abaixo devem ser executados na raiz do repositório.

## Requisitos e instalação

- Python **3.12 ou superior**, com `venv` e `pip` (a imagem Docker usa 3.12).
- GNU Make, Bash e Git.
- Para executar os nós, testes de ponta a ponta e novas coletas: Docker
  Engine em execução e **Docker Compose v2** (`docker compose`), com acesso
  ao daemon pelo usuário atual.

```bash
make install
make help
```

`make install` cria `.venv/` e instala o pacote em modo editável, `pytest`,
`pytest-asyncio`, `pandas` e `matplotlib`. Os demais alvos escolhem
automaticamente `.venv/bin/python` quando ele existe; não é necessário
ativar o ambiente. A instalação e o primeiro build precisam de acesso à
internet para baixar dependências e a imagem base.

## Testes automatizados

```bash
make test
```

A suíte padrão inclui testes dos algoritmos no simulador em memória
(`FakeTransport`/`FakeClock`), transporte UDP em localhost, configuração,
logs, injeção de falhas e scripts de execução e análise. Os testes de
algoritmos usam tempo virtual e sementes fixas. Docker não é necessário
para essa suíte; o ambiente precisa permitir sockets UDP locais.

Para executar apenas um módulo ou cenário:

```bash
.venv/bin/python -m pytest tests/test_heartbeat.py
.venv/bin/python -m pytest tests/test_bully.py
.venv/bin/python -m pytest -k crash
```

Os testes com contêineres são separados:

```bash
make test-e2e
```

Eles verificam comunicação entre nós, eleição inicial, crash e retorno do
líder, pausa/retomada e o pipeline de coleta/análise. Usam os projetos
isolados `mc714-e2e` e `mc714-e2e-exp`, com remoção do ambiente ao final.
Se Docker estiver indisponível, esses testes são marcados como **skipped**;
isso não equivale a uma execução bem-sucedida dos cenários com Docker.

## Executar e reproduzir falha e recuperação

```bash
make build
make up N=5
make ps
make logs-f
```

O projeto padrão é `mc714`, com serviços `node1` a `node5`. Após a
estabilização, todos devem anunciar `leader_change` com `leader=5`.
`make logs-f` acompanha os logs; interrompa com Ctrl+C para continuar
usando o terminal (os contêineres continuam executando) - Nesse caso os nós serão poluídos, então é possível filtrar através da mensagem "leader_change". Utilize o comando make logs | grep "leader_change".

Um roteiro reproduzível, sem perdas artificiais:

```bash
# Aguarda a eleição inicial e verifica a precisão do detector.
sleep 5
make check-hb ARGS=--strict-accuracy

# Derruba o líder: os sobreviventes devem suspeitar do nó 5 e eleger o nó 4.
make kill NODE=5
sleep 3
make logs
make check-hb ARGS=--strict-accuracy

# Recupera o nó 5: espera-se restore e retomada da liderança pelo maior ID.
make start NODE=5
sleep 3
make logs
make check-hb ARGS=--strict-accuracy

# Salva a evidência antes de remover os contêineres.
make -s logs > .run/demo-logs.jsonl
make down
```

As ações `kill`, `stop`, `start`, `pause` e `unpause` recebem `NODE=<id>` e
registram o comando em `.run/faults.jsonl`. Para simular um nó lento:

```bash
make up N=5
sleep 5
make pause NODE=2
sleep 2
make unpause NODE=2
sleep 3
make check-hb
make down
```

`make check-hb` verifica os logs atuais junto às falhas registradas;
`--strict-accuracy` também faz o comando falhar se houver falsa suspeita
segundo o verificador. A verificação de acordo e recuperação do Bully é
feita pelos testes e pelos analisadores de experimentos.

`make up` inicia uma nova sessão de falhas e apaga `.run/faults.jsonl`.
`make down` remove os contêineres e a rede; exporte os logs antes dele.
Para N diferente de 5, o Compose é gerado em `.run/compose.yaml`, usado
pelos demais alvos até `make down`. O `compose.yaml` versionado permanece
com cinco nós.

## Configuração dos nós

As variáveis são passadas pelo ambiente do host ao Compose:

```bash
DROP_RATE=0.1 DELAY_MS=10 JITTER_MS=5 SEED=3 make up N=7
```

| Variável | Padrão | Significado |
|---|---|---|
| `HB_INTERVAL_MS` | `200` | Intervalo de envio de heartbeat |
| `HB_TIMEOUT_MS` | `600` | Prazo sem heartbeat até suspeitar; deve exceder o intervalo |
| `HB_ADAPTIVE` | `false` | Habilita aumento do timeout após falsa suspeita |
| `HB_TIMEOUT_STEP_MS` | `200` | Incremento do timeout adaptativo |
| `HB_TIMEOUT_MAX_MS` | `0` | Teto adaptativo; zero significa sem teto |
| `ELECTION_TIMEOUT_MS` | `500` | Espera por ANSWER na eleição |
| `COORD_TIMEOUT_MS` | `1000` | Espera pelo anúncio COORDINATOR |
| `DROP_RATE` | `0.0` | Probabilidade de perda, entre 0 e 1 |
| `DELAY_MS` / `JITTER_MS` | `0` / `0` | Atraso e variação artificial em milissegundos |
| `SEED` | `0` | Semente da aleatoriedade, combinada com o ID do nó |

O Compose preenche `NODE_ID`, `PEERS` e a porta UDP `9000` de cada nó.
A perda artificial afeta tanto heartbeats quanto mensagens de eleição.
As sementes tornam a aleatoriedade controlável; os tempos medidos com
Docker também dependem do escalonamento e da carga do host.

## Experimentos: reanálise e nova coleta

As especificações executáveis estão em
[`experiments/configs/`](experiments/configs/). Cada TOML define parâmetros
base, varreduras, sementes, aquecimento, duração, falhas e gráficos.

| Experimento (`EXP`) | Parâmetro variado | Seeds por configuração | Aquecimento + medição por ensaio |
|---|---|---|---|
| `hb-timeout` | Timeout: 400/600/1000 ms; dois crashes com retorno | 10 | 5 + 28 s |
| `hb-scale` | N: 3/5/7/9; sem crashes | 10 | 5 + 12 s |
| `hb-loss` | Perda: 0/5/10/20%; timeout fixo/adaptativo; crash ao final | 10 | 5 + 40 s |
| `bully-election` | N: 3/5/7/9/11/13; crash do maior ID | 10 | 10 + 8 s |
| `bully-loss` | Perda: 0/5/10/20%; sem crashes | 10 | 5 + 30 s |
| `bully-timeout-election` | Espera por ANSWER: 200/500/1000 ms | 3 | 3 + 5 s |
| `bully-timeout-heartbeat` | Timeout do detector: 400/600/1200 ms | 3 | 3 + 5 s |

Para **reanalisar os dados brutos versionados**, sem executar contêineres:

```bash
make analyze
make analyze EXP=bully-election
```

Para executar e analisar uma varredura, um piloto ou todas as especificações:

```bash
make experiments EXP=bully-election
make experiments EXP=hb-loss SEEDS=1
make experiments
```

O executor retoma ensaios interrompidos e **pula os que já têm
`complete: true` no `meta.json`**. Como há dados brutos no repositório,
esses comandos podem somente reutilizá-los. `SEEDS=1` seleciona a primeira
semente de cada configuração; não obriga uma nova coleta.

Para uma **nova coleta separada**, sem sobrescrever os dados versionados:

```bash
.venv/bin/python scripts/run_experiment.py experiments/configs/bully-election.toml \
  --seeds 1 --out .run/repro/raw --dry-run
.venv/bin/python scripts/run_experiment.py experiments/configs/bully-election.toml \
  --seeds 1 --out .run/repro/raw
.venv/bin/python scripts/analyze.py .run/repro/raw/bully-election \
  --out .run/repro/results
```

Retire `--seeds 1` para usar todas as sementes do TOML. `--dry-run` lista
os ensaios pendentes e estima o tempo, incluindo uma margem para operações
Docker. A execução completa das sete varreduras soma cerca de **2h10min
apenas de aquecimento e medição**, mais builds, inicializações e remoções;
o tempo real depende da máquina. `--force` refaz ensaios completos e
sobrescreve seus artefatos na saída selecionada.

As coletas usam o projeto Docker `mc714-exp`. Execute uma coleta por vez:
processos com esse mesmo projeto e arquivo Compose podem interferir entre si.
Os valores do TOML controlam a configuração de cada ensaio.

### Artefatos e métricas

```text
experiments/raw/<experimento>/
  env.json                         # hardware, SO, versões e identificação do código
  spec.toml                        # cópia da especificação executada
  <configuração>/seed-01/
    logs.jsonl.gz                  # eventos de todos os nós
    faults.jsonl                   # comandos de falha e seus instantes
    meta.json                      # parâmetros, seed, janela de medição e complete
experiments/results/<experimento>/
  runs.csv                         # métricas por ensaio e violações
  samples.csv                      # amostras individuais
  summary.csv                      # agregados, desvio e p50/p95/p99
  *.png / *.pdf                    # gráficos definidos no TOML
```

O heartbeat mede latência de detecção/recuperação, falsas suspeitas e
mensagens por segundo. O Bully mede tempo de eleição, tempo de failover
e tentativas locais espúrias sob perda.

Em `bully-election`, failover vai do disparo do comando de falha ao acordo
dos sobreviventes; eleição vai do primeiro `election_start` após a falha
ao acordo. Os instantes de início/fim do comando Docker delimitam a
incerteza sobre o crash. O acordo é observado até o fim da janela, sem
garantia além dela. Há detalhes em
[`experiments/README.md`](experiments/README.md) e na
[descrição de perda do Bully](experiments/configs/bully-loss.md).

### Scripts complementares e figuras


`run_bully_messages.py` executa N=3/5/7/9, melhor/pior caso e dez sementes,
usando os algoritmos de produção no simulador. Gera logs, metadados,
`runs.csv`, `summary.csv` e `messages.png` sob a raiz de `--out`.
Aceita `--seeds 1` para um piloto e refaz os ensaios a cada execução.
É um experimento controlado de contagem de mensagens; seus tempos são
virtuais. A metodologia está no
[README dos resultados](experiments/results/bully-messages-sim/README.md).

`heartbeat_report.py` consolida `hb-timeout`, `hb-scale` e os heartbeats
dos ensaios de `bully-loss` (reutilizados para medir falsas suspeitas).
Escreve CSVs e proveniência em `experiments/results/heartbeat-report/`
e figuras em `images/heartbeat-resultados.{png,pdf}`. Ele espera as
quantidades completas de ensaios, não uma coleta piloto. Essa consolidação
é diferente da varredura independente `hb-loss`, que compara timeout fixo
e adaptativo.

Os notebooks leem os CSVs já analisados e exportam figuras em
`experiments/results/`. Veja o [guia de notebooks](notebook/README.md)
para execução por células e destinos específicos.

| Script | Uso |
|---|---|
| `scripts/gen_compose.py` | Gera Compose para N nós; `make compose` atualiza o de N=5 |
| `scripts/faults.sh` | Injeta e registra falhas; também aceita vários IDs |
| `scripts/check_heartbeat.py` | Verifica completude, recuperação e precisão nos logs |
| `scripts/run_experiment.py` | Planeja, coleta e retoma ensaios TOML |
| `scripts/analyze.py` | Analisa dados brutos; `--no-plots` gera apenas CSVs |
| `scripts/collect_env.py` | Registra ambiente; disponível também por `make env` |

## Limites do ambiente

Os contêineres compartilham uma única máquina: não constituem domínios de
falha independentes. A comparação dos timestamps entre nós nas métricas
pressupõe esse host comum. Perdas, atrasos ou pausas podem produzir falsas
suspeitas e líderes divergentes temporariamente; o Bully depende de
timeouts adequados e comunicação estável para convergir.
