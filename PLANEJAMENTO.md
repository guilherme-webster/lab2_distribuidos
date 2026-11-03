# Planejamento — Trabalho 2 MC714 (2s2026)

**Algoritmos:** Eleição de líder **Bully (Valentão)** + **Detector de falhas por heartbeat**
**Entrega:** 13/11/2026 (Classroom: PDF do relatório com nomes/RAs, link do GitLab e link do vídeo)

Os dois algoritmos cobrem os dois grupos exigidos pelo enunciado:

| Grupo do enunciado | Algoritmo |
|---|---|
| Ordenação de eventos / exclusão mútua / **eleição** / snapshot | Bully |
| Consenso / replicação / **detecção de falhas** / P2P / coordenação | Heartbeat |

Além disso, eles se integram naturalmente: o detector de heartbeat é quem percebe que o líder caiu e dispara a eleição Bully. Isso já atende ao bônus "integração dos algoritmos para resolver um problema prático coerente" (serviço com líder tolerante a falhas).

---

## 1. Requisitos do enunciado → onde são atendidos

| Requisito | Como atender | Fase |
|---|---|---|
| 2 algoritmos de grupos diferentes | Bully + Heartbeat | 1–3 |
| ≥ 3 nós lógicos, cada um processo/contêiner independente | `compose.yaml` com N serviços (`node1..nodeN`), N parametrizável (3 a 9) | 0 |
| Comunicação só pela interface de rede da aplicação | UDP + JSON entre contêineres; nada de estado compartilhado | 0 |
| Timeouts explícitos | Todo `await` de rede com timeout; timeouts do Bully e do detector configuráveis via env | 1–2 |
| Não usar biblioteca que já implemente o algoritmo | Transporte "cru" (asyncio UDP); detector e eleição escritos do zero | — |
| Modelo do sistema declarado e justificado (tempo, canais, falhas, relógios/persistência, safety/liveness) | Seção 5 deste plano → vira seção do relatório | 1–2 |
| Correção demonstrada por testes automatizados ou verificação reproduzível de logs | Testes unitários (simulador determinístico) + verificador de logs JSON (`scripts/check_*.py`) | 1–3 |
| `compose.yaml` + comandos documentados p/ construir, iniciar, testar e remover | `Makefile` (`build`, `up`, `test`, `down`, `experiments`) + README | 0, 5 |
| Falhas repetíveis, com scripts e sementes registradas | Injeção na aplicação (perda/atraso com `SEED`) + `docker kill/stop/start/pause`; seed gravada em cada execução | 0, 4 |
| Por algoritmo: ≥1 cenário normal e ≥1 de falha | Ver Seção 6 | 4 |
| Por algoritmo: ≥1 métrica justificada | Heartbeat: tempo de detecção, taxa de falsos positivos. Bully: tempo de eleição, nº de mensagens | 4 |
| Variar ≥1 parâmetro em ≥3 níveis | N ∈ {3,5,7,9}; perda ∈ {0,5,10,20}%; timeout/intervalo ∈ {2,3,5} | 4 |
| Repetições + tendência/variabilidade + percentis (p50/p95/p99) | ≥10 seeds por configuração; média, desvio, p50/p95/p99 | 4 |
| Registrar duração, aquecimento, hardware, SO, versões, config | `scripts/collect_env.sh` + `meta.json` por execução | 4 |
| Análise à luz da complexidade de mensagens | Bully O(n) melhor caso / O(n²) pior caso; heartbeat all-to-all O(n²) msgs por período | 4–5 |
| Relatório IEEE 2 colunas, ≤ 6 páginas (+ refs), cópia no repositório | `report/` (LaTeX IEEEtran) | 5 |
| Vídeo ~5 min com os dois: cenário normal, falha injetada, recuperação | Roteiro na Seção 8 | 6 |
| Repositório com código, testes, scripts, **dados brutos**, relatório | `experiments/raw/` versionado | 5 |
| Discutir limitação de contêineres numa única máquina (não são domínios de falha independentes) | Seção de limitações do relatório | 5 |

**Pesos:** correção+testes 30 · modelo/arquitetura/falhas 20 · experimentos/reprodutibilidade 25 · relatório 15 · vídeo 10. → Testes e experimentos automatizados são 55% da nota; priorizar.

---

## 2. Decisões técnicas (propostas)

- **Linguagem:** Python 3.12 com `asyncio` (sem dependências pesadas). Testes com `pytest`. Análise com `pandas` + `matplotlib`.
- **Transporte:** UDP com mensagens JSON. Justificativa: heartbeat é naturalmente datagrama; perda/atraso ficam explícitos no modelo do canal (o enunciado pede que se discuta a semântica de entrega). Bully trata perdas via timeouts.
- **Injeção de falhas:**
  - *Nível de aplicação* (principal, reprodutível): camada no transporte que descarta (`DROP_RATE`) e atrasa (`DELAY_MS`, `JITTER_MS`) mensagens com `random.Random(SEED + node_id)`; lista de bloqueio para simular partições.
  - *Nível de contêiner*: `docker kill` (crash-stop), `docker stop` + `start` (crash-recovery), `docker pause/unpause` (nó lento → provoca falso positivo).
  - *Opcional:* `tc netem` (exige `cap_add: NET_ADMIN`) — só se sobrar tempo.
- **Observabilidade:** cada nó escreve **um evento JSON por linha** no stdout (`ts`, `node`, `event`, campos). Esses logs são a evidência de correção e a fonte das métricas. Como todos os contêineres rodam no mesmo host, `ts` é comparável entre nós (discutir no relatório: na nuvem isso não valeria).
- **Configuração:** tudo por variável de ambiente (`NODE_ID`, `PEERS`, `HB_INTERVAL_MS`, `HB_TIMEOUT_MS`, `ELECTION_TIMEOUT_MS`, `COORD_TIMEOUT_MS`, `DROP_RATE`, `SEED`, ...). O mesmo código roda local e na nuvem do IC mudando só configuração (exigência do enunciado).
- **Geração do compose:** `scripts/gen_compose.py N` gera `compose.yaml` para N nós (necessário para variar N nos experimentos). O `compose.yaml` versionado é o de N=5.

### Estrutura de diretórios proposta

```
src/dsnode/
  config.py        # leitura de env → dataclass Config
  messages.py      # tipos de mensagem + (de)serialização JSON
  transport.py     # UDP asyncio, registro de handlers por tipo, injeção de falhas
  log.py           # logger de eventos JSON estruturados
  heartbeat.py     # detector de falhas (callbacks on_suspect / on_restore)
  bully.py         # eleição Bully
  node.py          # main: monta config + transporte + detector + bully
tests/
  sim.py           # FakeTransport em memória + relógio controlável (testes determinísticos)
  test_heartbeat.py
  test_bully.py
  test_integration_sim.py
scripts/
  gen_compose.py   # compose para N nós
  faults.sh        # kill/stop/start/pause de nós
  run_experiment.py# roda cenário × parâmetros × seeds, salva logs brutos + meta.json
  check_heartbeat.py / check_bully.py   # verificadores de propriedades sobre logs
  analyze.py       # métricas, percentis, gráficos
  collect_env.sh   # hardware, SO, versões
experiments/
  raw/<cenario>/<config>/<seed>/{logs.jsonl, meta.json}
  results/         # CSVs agregados e figuras
report/            # LaTeX IEEEtran + PDF final
Dockerfile, compose.yaml, Makefile, README.md, pyproject.toml
```

---

## 3. Contratos entre os módulos (definir no dia 1!)

Esses contratos são o que permite que a dupla trabalhe em paralelo. Quem começa (Guilherme) cria os arquivos com essas assinaturas já no primeiro commit.

```python
# messages.py
@dataclass
class Message:
    type: str        # "HEARTBEAT" | "ELECTION" | "ANSWER" | "COORDINATOR"
    src: int         # id do remetente
    dst: int
    payload: dict    # ex.: {"seq": 12} ou {"election_id": "3-7"}
    msg_id: str      # único, para deduplicação e contagem

# transport.py  (FakeTransport em tests/sim.py implementa a mesma interface)
class Transport(Protocol):
    node_id: int
    peers: list[int]
    async def send(self, dst: int, msg: Message) -> None: ...   # best-effort, nunca bloqueia
    def on(self, msg_type: str, handler: Callable[[Message], Awaitable[None]]) -> None: ...

# heartbeat.py
class FailureDetector:
    def __init__(self, transport, clock, cfg, log): ...
    async def start(self) -> None: ...
    def is_suspected(self, peer: int) -> bool: ...
    def alive_peers(self) -> set[int]: ...
    def subscribe(self, on_suspect: Callable[[int], None], on_restore: Callable[[int], None]) -> None: ...

# bully.py
class Bully:
    def __init__(self, transport, clock, cfg, log, detector): ...
    async def start(self) -> None: ...          # na inicialização, convoca eleição
    def leader(self) -> int | None: ...
    async def start_election(self, reason: str) -> None: ...
```

**Eventos de log mínimos** (nomes fixos, os verificadores dependem deles):

| Evento | Campos | Quem emite |
|---|---|---|
| `node_start` / `node_stop` | `config` | node.py |
| `msg_send` / `msg_recv` / `msg_drop` | `type`, `peer`, `msg_id` | transport |
| `suspect` / `restore` | `peer`, `last_seen_ms` | heartbeat |
| `election_start` | `election_id`, `reason` | bully |
| `answer_recv`, `coordinator_sent` | `election_id`, `peer` | bully |
| `leader_change` | `leader`, `election_id` | bully |

---

## 4. Divisão de tarefas da dupla

A divisão segue a dependência: **Guilherme começa pela fundação + heartbeat**, que não dependem de nada do colega. O colega desenvolve o Bully **contra o `FakeTransport`** desde o primeiro dia, sem esperar o transporte real.

### Guilherme — Fundação, Heartbeat e infraestrutura de experimentos

1. **Fundação (prioridade máxima — desbloqueia o colega)**
   - esqueleto do repositório, `pyproject.toml`, `.gitignore`
   - `config.py`, `messages.py`, `log.py` com os contratos da Seção 3
   - `tests/sim.py`: `FakeTransport` + `FakeClock` (rede em memória com perda/atraso controlados por seed)
   - `transport.py` UDP real + injeção de falhas na aplicação
   - `Dockerfile`, `scripts/gen_compose.py`, `compose.yaml`, `Makefile` (`build/up/down/logs/test`)
2. **Heartbeat**
   - envio periódico all-to-all a cada `HB_INTERVAL_MS`
   - suspeita após `HB_TIMEOUT_MS` sem notícias; `restore` se o nó voltar a responder
   - (extra que vale a pena) timeout adaptativo: ao detectar falso positivo, aumenta o timeout daquele peer → argumento de ◇P sob sincronia parcial
   - testes unitários no simulador (completude, precisão sem falhas, restore)
   - `scripts/check_heartbeat.py`
3. **Infra de experimentos (genérica, usada pelos dois)**
   - `scripts/faults.sh`, `scripts/run_experiment.py`, `scripts/collect_env.sh`
   - experimentos do heartbeat + gráficos
4. **Relatório:** arquitetura, modelo de sistema do heartbeat, injeção de falhas, resultados do heartbeat.

### Colega — Bully, integração e verificação de eleição

1. **Bully** (pode começar no dia 1 usando `FakeTransport`)
   - mensagens `ELECTION`, `ANSWER`, `COORDINATOR`
   - timeouts `ELECTION_TIMEOUT_MS` (esperando ANSWER) e `COORD_TIMEOUT_MS` (esperando COORDINATOR após receber ANSWER → reinicia eleição)
   - nó que volta (crash-recovery) com ID maior convoca eleição e assume ("bully")
   - `election_id` para correlacionar mensagens nos logs e contar mensagens por eleição
   - testes unitários no simulador (líder = maior ID vivo; crash do líder; crash durante a eleição; retorno do maior ID)
2. **Integração** (depende do heartbeat — fazer depois que ele existir)
   - `node.py` liga `FailureDetector.on_suspect(leader)` → `Bully.start_election()`
   - teste de integração no simulador e no compose
3. **Verificação e experimentos do Bully**
   - `scripts/check_bully.py`
   - cenários e varredura de N usando o `run_experiment.py`
4. **Relatório:** modelo de sistema do Bully, propriedades de correção, resultados do Bully, discussão de partição/split-brain.

### Compartilhado
- Revisão cruzada: cada um revisa os commits/MR do outro.
- Introdução, conclusão, limitações do relatório.
- Vídeo (os dois precisam aparecer).
- README com instruções de reprodução.
- Tag de entrega no GitLab da disciplina.

---

## 5. Modelo do sistema (rascunho para o relatório)

### Heartbeat
- **Tempo:** parcialmente síncrono — existem limites para atraso e velocidade de processamento, mas desconhecidos/válidos só após um GST. O detector usa timeouts (e, opcionalmente, timeout adaptativo).
- **Canais:** UDP — sem garantia de entrega, ordem ou ausência de duplicação; a injeção de falhas acrescenta perda e atraso controlados. Heartbeats carregam `seq` crescente para descartar velhos/duplicados.
- **Falhas:** crash-stop e crash-recovery (nó reaparece com estado vazio); "nó lento" via `docker pause`.
- **Relógios:** só relógio monotônico local para medir timeouts; nenhum relógio sincronizado é necessário. Para as métricas, o `ts` dos logs vem do mesmo host.
- **Propriedades:**
  - *Completude forte:* todo nó que falha é, em algum momento, suspeito permanentemente por todo nó correto (limite: `HB_TIMEOUT + HB_INTERVAL + atraso`).
  - *Precisão:* sem perdas/atrasos, nenhum nó correto é suspeito (precisão forte no cenário normal); com perdas, precisão eventual (◇P) se o timeout for adaptativo. Quantificar falsos positivos.

### Bully
- **Tempo:** o Bully clássico **assume sistema síncrono** (timeouts corretos ⇒ detecção perfeita). Declarar isso e mostrar experimentalmente o que acontece quando a hipótese é violada (perda alta / nó pausado ⇒ eleições extras, líderes transitórios divergentes).
- **Canais:** os mesmos do heartbeat; perda de `ANSWER`/`COORDINATOR` é tratada por timeouts + nova eleição.
- **Falhas:** crash-stop e crash-recovery; partições discutidas (bônus) — cada lado elege seu líder (split-brain), comportamento esperado do algoritmo.
- **Persistência:** nenhuma; nó que reinicia convoca eleição.
- **Propriedades:**
  - *Safety (acordo):* após estabilização, todos os nós corretos têm o mesmo líder, e ele é o maior ID vivo.
  - *Liveness (terminação):* se o líder falha, em tempo finito (limitado pelos timeouts) um novo líder é eleito e conhecido por todos os corretos.
- **Complexidade:** melhor caso n−2 mensagens `COORDINATOR` (o segundo maior detecta a falha); pior caso O(n²) (o menor ID detecta).

---

## 6. Cenários e experimentos

Configuração base: N=5, `HB_INTERVAL=200ms`, `HB_TIMEOUT=3×intervalo`, aquecimento de 10 s antes de injetar falha, 10 seeds por configuração.

### Cenários de correção (testes + verificação de logs)

| # | Algoritmo | Cenário | Verificação |
|---|---|---|---|
| H1 | Heartbeat | normal, sem perdas | 0 suspeitas em todos os nós |
| H2 | Heartbeat | `docker kill` de um nó | todos os vivos suspeitam dele em ≤ limite |
| H3 | Heartbeat | `docker pause` 2 s + `unpause` | `suspect` seguido de `restore` |
| B1 | Bully | inicialização | todos concordam em líder = maior ID |
| B2 | Bully | crash do líder | novo líder = maior ID vivo, conhecido por todos |
| B3 | Bully | líder volta (crash-recovery) | ele retoma a liderança |
| B4 | Bully | crash do próximo candidato durante a eleição | eleição termina com o próximo maior |
| I1 | Integrado | líder morre → heartbeat detecta → Bully elege | tempo total crash→novo líder |
| P1 *(bônus)* | Integrado | partição {1,2} / {3,4,5} e cura | split-brain durante a partição; convergência após cura |

### Experimentos de desempenho

| Algoritmo | Métrica | Parâmetro variado (≥3 níveis) |
|---|---|---|
| Heartbeat | tempo de detecção (p50/p95/p99) | `HB_TIMEOUT` ∈ {2,3,5}× intervalo |
| Heartbeat | taxa de falsos positivos por minuto | perda ∈ {0, 5, 10, 20}% |
| Heartbeat | mensagens/s (overhead) | N ∈ {3, 5, 7, 9} → esperado ∝ n² |
| Bully | tempo de eleição (crash → todos conhecem o novo líder) | N ∈ {3, 5, 7, 9} |
| Bully | nº de mensagens por eleição | N ∈ {3,5,7,9} × quem detecta (2º maior vs. menor ID) → O(n) vs O(n²) |
| Bully | nº de eleições espúrias | perda ∈ {0, 5, 10, 20}% |

Cada execução grava em `experiments/raw/...`: `logs.jsonl` (todos os nós), `meta.json` (parâmetros, seed, duração, aquecimento, commit git) e uma vez por máquina o `env.json` (CPU, RAM, SO, kernel, versões de Docker/Compose/Python).

---

## 7. Cronograma (03/11 → 13/11)

| Data | Guilherme | Colega |
|---|---|---|
| **03–04/11** | Fundação: contratos, `sim.py`, `config/messages/log`, transporte UDP, Dockerfile, compose, Makefile. Push o quanto antes. | Estuda Bully (van Steen & Tanenbaum, cap. 5), escreve modelo do sistema do Bully, começa `bully.py` contra `FakeTransport`. |
| **05–06/11** | Heartbeat + testes + `check_heartbeat.py`; `faults.sh`. | Bully + testes unitários (B1–B4) + `check_bully.py`. |
| **07/11** | `run_experiment.py`, `collect_env.sh`, `analyze.py` (esqueleto). | Integração em `node.py` (I1), teste no compose. |
| **08–09/11** | Experimentos do heartbeat + gráficos. | Experimentos do Bully + gráficos; cenário P1 (bônus) se der tempo. |
| **09–11/11** | Relatório: arquitetura, heartbeat, falhas. | Relatório: Bully, integração, correção. |
| **11–12/11** | Juntos: introdução/conclusão/limitações, README de reprodução, gravar vídeo, revisão final, tag de entrega. | |
| **13/11** | Folga / submissão no Classroom (uma pessoa). | |

**Marco crítico:** fundação mergeada até o fim de 04/11. Sem ela, o colega fica preso ao simulador.

---

## 8. Roteiro do vídeo (~5 min, os dois falam)

1. (0:30) Problema e arquitetura: N nós, heartbeat + Bully, como se integram.
2. (1:00) `make up` com 5 nós: líder = nó 5, heartbeats fluindo (logs filtrados).
3. (1:30) Falha: `docker kill node5` → `suspect` nos outros → eleição → nó 4 vira líder.
4. (1:00) Recuperação: `docker start node5` → `restore` → nó 5 retoma a liderança.
5. (1:00) Um gráfico de cada algoritmo + uma conclusão de cada.

---

## 9. Checklist final de entrega

- [ ] `make build && make up && make test && make down` funcionam numa máquina limpa
- [ ] `make experiments` reproduz todos os dados (e documenta o tempo que leva)
- [ ] `experiments/raw/` com dados brutos versionados
- [ ] Relatório IEEE ≤ 6 páginas + refs, cópia em `report/`
- [ ] Relatório cita fontes de qualquer código reutilizado
- [ ] Primeira página do PDF: nomes + RAs, link do GitLab, link do vídeo
- [ ] Vídeo ~5 min com os dois
- [ ] Tag de entrega criada no GitLab da disciplina
- [ ] Submissão no Classroom por uma pessoa da dupla
