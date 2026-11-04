# AGENTS.md

Instruções para agentes de código (Claude Code, Codex, etc.) que trabalham neste repositório.

## Contexto do projeto

Trabalho 2 de MC714 (Sistemas Distribuídos, Unicamp, 2s2026), feito em dupla. Implementamos dois algoritmos distribuídos que se integram:

- **Detector de falhas por heartbeat** (`src/dsnode/heartbeat.py`)
- **Eleição de líder Bully** (`src/dsnode/bully.py`), disparada quando o detector suspeita do líder

Cada nó é um contêiner independente orquestrado por Docker Compose v2 (`compose.yaml`); os nós se comunicam só pela rede (UDP + JSON). O enunciado está em `Trabalho2-MC714_2s2026.pdf` e o plano completo (requisitos, contratos entre módulos, divisão de tarefas, cronograma) em `PLANEJAMENTO.md`. **Leia o `PLANEJAMENTO.md` antes de começar uma tarefa.**

## Stack

- Python 3.12, `asyncio`, sem frameworks que já implementem os algoritmos (proibido pelo enunciado)
- `pytest` para testes; `pandas` + `matplotlib` para análise
- Docker Compose v2; o ambiente de referência é uma única máquina

## Comandos

Mantenha esta lista atualizada (`make help` lista os alvos existentes).

```
make install       # cria .venv e instala o pacote (dev + análise)
make test          # testes automatizados (simulador + transporte em localhost)
make test-e2e      # ponta a ponta com Docker, em projeto isolado (mc714-e2e)
make build         # constrói a imagem
make up N=5        # sobe N nós (N=5 usa o compose.yaml; outros N geram .run/compose.yaml)
make ps            # estado dos contêineres
make logs          # logs JSON de todos os nós, ordenados por ts
make logs-f        # acompanha os logs ao vivo
make down          # remove todo o ambiente
make kill NODE=3   # injeta falha (também stop/start/pause/unpause), registrada em .run/faults.jsonl
make check-hb      # verifica o heartbeat nos logs atuais + falhas (ARGS=--strict-accuracy)
make compose       # regenera o compose.yaml versionado
make experiments   # (a fazer) roda os experimentos e grava em experiments/raw/
```

Parâmetros dos algoritmos e da injeção de falhas vão pelo ambiente do host:
`DROP_RATE=0.1 SEED=3 make up N=7`.

## Commits: pequenos, modulares e funcionais

**Não faça commits a menos que o usuário peça explicitamente.** Quando pedirem, ou quando sugerir como dividir o trabalho em commits, siga estas regras:

1. **Um commit = um módulo funcional ou uma mudança coesa.** Ex.: "transporte UDP com handlers por tipo" é um commit; "injeção de perda/atraso no transporte" é outro. Não misture módulos diferentes nem refatoração com funcionalidade nova.
2. **Cada commit deixa o projeto funcionando:** importa sem erro e `make test` passa. Nada de commits "WIP" ou quebrados.
3. **Código e seus testes vão no mesmo commit.**
4. **Mensagens em Conventional Commits, em português**, com escopo do módulo:
   - `feat(transport): envio UDP com registro de handlers por tipo de mensagem`
   - `feat(heartbeat): suspeita de peer após timeout sem heartbeat`
   - `test(bully): cenário de crash do líder durante a eleição`
   - `chore(compose): gerador de compose.yaml para N nós`
   - `exp(heartbeat): varredura de taxa de perda 0/5/10/20%`
   - `docs(report): seção de modelo do sistema do Bully`
   Tipos: `feat`, `fix`, `test`, `refactor`, `chore`, `docs`, `exp` (scripts/dados de experimento).
5. **Corpo da mensagem** (quando não for óbvio): o que muda e por quê, em 1–3 linhas.
6. Dados brutos de experimento (`experiments/raw/`) vão em commits `exp(...)` separados do código que os gerou.
7. Sequência esperada de commits na fundação, como referência de granularidade:
   `chore: estrutura do projeto` → `feat(config)` → `feat(messages)` → `feat(log)` → `test(sim): FakeTransport e FakeClock` → `feat(transport)` → `feat(transport): injeção de falhas` → `chore(docker)` → `chore(compose)` → `chore(make)`.

## Divisão de responsabilidades

| Área | Dono |
|---|---|
| `config.py`, `messages.py`, `log.py`, `transport.py`, `tests/sim.py`, Docker/compose/Makefile | Guilherme |
| `heartbeat.py`, `check_heartbeat.py`, `faults.sh`, `run_experiment.py`, `collect_env.sh` | Guilherme |
| `bully.py`, `check_bully.py`, integração em `node.py` | Colega |

Os contratos (assinaturas e nomes de eventos de log) estão na Seção 3 do `PLANEJAMENTO.md`. **Não altere um contrato sem avisar o usuário**, porque o outro integrante depende dele. Mudanças no módulo do colega devem ser pontuais e explicitadas na resposta.

## Convenções de código

- Todo I/O de rede é assíncrono e **toda espera tem timeout explícito** (exigência do enunciado).
- Toda aleatoriedade usa `random.Random` semeado a partir de `SEED` (e `NODE_ID`); nunca `random` global. Experimentos precisam ser reprodutíveis.
- Toda configuração vem de variáveis de ambiente lidas em `config.py`; nada de constantes mágicas espalhadas. O mesmo código deve rodar local e na nuvem do IC mudando só configuração.
- Use relógio monotônico (`time.monotonic`) para timeouts; o relógio é injetado (`clock`) para que os testes usem `FakeClock`.
- **Logs são evidência de correção:** emita eventos JSON de uma linha com os nomes definidos no plano (`suspect`, `restore`, `election_start`, `leader_change`, `msg_send`, ...). Não renomeie eventos sem atualizar os verificadores em `scripts/check_*.py`.
- Lógica dos algoritmos não depende de Docker: tem que ser testável com `FakeTransport` em memória.
- Comentários e docstrings em português, curtos; nomes de identificadores em inglês.

## Testes

- Testes unitários usam o simulador (`tests/sim.py`) e devem ser determinísticos (seed fixa, `FakeClock`, sem `sleep` real).
- Cada propriedade de correção do plano (completude/precisão do detector; acordo/terminação do Bully) deve ter pelo menos um teste ou um verificador de logs.
- Ao terminar uma mudança, rode `make test` (ou `pytest`) e relate o resultado real.
