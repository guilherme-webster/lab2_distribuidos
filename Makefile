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

.DEFAULT_GOAL := help
.PHONY: help install test test-e2e build up ps logs logs-f down compose

help: ## lista os alvos
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-10s %s\n", $$1, $$2}'

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

compose: ## regenera o compose.yaml versionado (N=5)
	$(PYTHON) scripts/gen_compose.py 5 -o compose.yaml
