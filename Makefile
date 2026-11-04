# Atalhos para construir, subir, testar e remover o ambiente.
# Todos os comandos também podem ser rodados à mão (veja cada receita).

N ?= 5
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

# N=5 usa o compose.yaml versionado; outros N geram um compose em .run/.
# Os demais alvos usam o último gerado (se existir) até o `make down`.
RUN_COMPOSE ?= .run/compose.yaml
UP_FILE := $(if $(filter 5,$(N)),compose.yaml,$(RUN_COMPOSE))
CUR_FILE = $(if $(wildcard $(RUN_COMPOSE)),$(RUN_COMPOSE),compose.yaml)
COMPOSE = docker compose --project-directory . -f
FAULTS_FILE ?= .run/faults.jsonl
FAULT = RUN_COMPOSE=$(RUN_COMPOSE) FAULTS_FILE=$(FAULTS_FILE) scripts/faults.sh
# Experimentos: EXP=hb-loss roda um só; SEEDS=2 faz um piloto rápido
EXP ?= $(basename $(notdir $(wildcard experiments/configs/*.toml)))
SEEDS_ARG = $(if $(SEEDS),--seeds $(SEEDS))

.DEFAULT_GOAL := help
.PHONY: help install test test-e2e build up ps logs logs-f down compose \
	kill stop start pause unpause check-hb experiments analyze env

help: ## lista os alvos
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-12s %s\n", $$1, $$2}'

install: ## cria .venv e instala o pacote com dependências de dev e análise
	python3 -m venv .venv
	.venv/bin/pip install -e '.[dev,analysis]'

test: ## testes automatizados (simulador + transporte em localhost)
	$(PYTHON) -m pytest

test-e2e: ## testes de ponta a ponta com Docker (projeto isolado mc714-e2e)
	$(PYTHON) -m pytest -m docker

build: ## constrói a imagem do nó
	$(COMPOSE) compose.yaml build

up: ## sobe N nós em segundo plano (padrão N=5)
	rm -f $(FAULTS_FILE)
ifeq ($(N),5)
	rm -f $(RUN_COMPOSE)
else
	mkdir -p .run
	$(PYTHON) scripts/gen_compose.py $(N) -o $(RUN_COMPOSE)
endif
	$(COMPOSE) $(UP_FILE) up -d --build --remove-orphans

ps: ## estado dos contêineres
	$(COMPOSE) $(CUR_FILE) ps -a

logs: ## logs JSON de todos os nós, ordenados por ts
	@$(COMPOSE) $(CUR_FILE) logs --no-log-prefix | sort

logs-f: ## acompanha os logs ao vivo
	$(COMPOSE) $(CUR_FILE) logs -f

down: ## remove contêineres e rede do ambiente
	$(COMPOSE) $(CUR_FILE) down --remove-orphans -t 2
	rm -f $(RUN_COMPOSE)

kill: ## crash do nó NODE (SIGKILL), registrado em .run/faults.jsonl
	$(FAULT) kill $(NODE)

stop: ## parada limpa do nó NODE
	$(FAULT) stop $(NODE)

start: ## religa o nó NODE
	$(FAULT) start $(NODE)

pause: ## congela o nó NODE
	$(FAULT) pause $(NODE)

unpause: ## descongela o nó NODE
	$(FAULT) unpause $(NODE)

check-hb: ## verifica o heartbeat nos logs atuais e nas falhas registradas
	@mkdir -p .run
	@$(COMPOSE) $(CUR_FILE) logs --no-log-prefix > .run/logs.jsonl
	$(PYTHON) scripts/check_heartbeat.py .run/logs.jsonl $(wildcard $(FAULTS_FILE)) $(ARGS)

experiments: ## roda e analisa os experimentos (EXP=nome, SEEDS=n; retoma se interrompido)
	@for e in $(EXP); do \
		$(PYTHON) scripts/run_experiment.py experiments/configs/$$e.toml $(SEEDS_ARG) && \
		$(PYTHON) scripts/analyze.py experiments/raw/$$e || exit 1; \
	done

analyze: ## só reanalisa os dados brutos (EXP=nome)
	@for e in $(EXP); do $(PYTHON) scripts/analyze.py experiments/raw/$$e || exit 1; done

env: ## mostra hardware, SO e versões registrados nos experimentos
	$(PYTHON) scripts/collect_env.py

compose: ## regenera o compose.yaml versionado (N=5)
	$(PYTHON) scripts/gen_compose.py 5 -o compose.yaml
