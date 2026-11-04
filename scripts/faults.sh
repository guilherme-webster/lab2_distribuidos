#!/usr/bin/env bash
# Injeta falhas em nós do ambiente compose e registra cada uma em JSONL.
#
#   scripts/faults.sh kill 3          # crash-stop (SIGKILL, sem node_stop)
#   scripts/faults.sh stop 3 4        # parada limpa (SIGTERM, com node_stop)
#   scripts/faults.sh start 3         # religa (crash-recovery, nova encarnação)
#   scripts/faults.sh pause 3         # congela o processo (nó lento)
#   scripts/faults.sh unpause 3
#
# Cada falha vira uma linha em $FAULTS_FILE (padrão .run/faults.jsonl), no
# formato lido por scripts/check_heartbeat.py:
#
#   {"ts": ..., "event": "fault", "action": "kill", "target": 3, "issued_ts": ..., "cmd_ms": ...}
#
# ts é o instante logo após o comando terminar (a falha já ocorreu); issued_ts
# é o instante anterior a ele. A diferença (cmd_ms) é a incerteza sobre o
# momento exato da falha. Usa o mesmo compose que o Makefile: $RUN_COMPOSE
# (padrão .run/compose.yaml) se existir, senão compose.yaml; COMPOSE_PROJECT_NAME
# é respeitado.

set -euo pipefail

cd "$(dirname "$0")/.."

ACTIONS="kill stop start pause unpause"
RUN_COMPOSE="${RUN_COMPOSE:-.run/compose.yaml}"
FAULTS_FILE="${FAULTS_FILE:-.run/faults.jsonl}"

usage() {
    echo "uso: $0 {${ACTIONS// /|}} NÓ [NÓ ...]" >&2
    exit 2
}

[[ $# -ge 2 ]] || usage
action="$1"
shift
[[ " $ACTIONS " == *" $action "* ]] || usage
for node in "$@"; do
    [[ "$node" =~ ^[0-9]+$ ]] || { echo "nó inválido: $node" >&2; exit 2; }
done

compose_file=compose.yaml
[[ -f "$RUN_COMPOSE" ]] && compose_file="$RUN_COMPOSE"
mkdir -p "$(dirname "$FAULTS_FILE")"

# date +%N não existe no macOS; lá cai para o python3
if [[ "$(date +%N)" =~ ^[0-9]+$ ]]; then
    now() { date +%s.%N; }
else
    now() { python3 -c 'import time; print(f"{time.time():.6f}")'; }
fi

for node in "$@"; do
    issued=$(now)
    # stop: -t 2 como no make down, para não esperar os 10 s padrão
    extra=()
    [[ "$action" == stop ]] && extra=(-t 2)
    if ! output=$(docker compose --project-directory . -f "$compose_file" "$action" \
            ${extra[@]+"${extra[@]}"} "node$node" 2>&1); then
        echo "falhou: docker compose $action node$node" >&2
        echo "$output" >&2
        exit 1
    fi
    done_ts=$(now)
    cmd_ms=$(awk -v a="$issued" -v b="$done_ts" 'BEGIN { printf "%.1f", (b - a) * 1000 }')
    record=$(printf '{"ts":%s,"event":"fault","action":"%s","target":%s,"issued_ts":%s,"cmd_ms":%s}' \
        "$done_ts" "$action" "$node" "$issued" "$cmd_ms")
    echo "$record" >> "$FAULTS_FILE"
    echo "$record"
done
