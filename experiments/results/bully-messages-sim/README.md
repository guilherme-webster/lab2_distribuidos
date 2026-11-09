# Mensagens por eleição: melhor e pior caso

Execução: `.venv/bin/python scripts/run_bully_messages.py`.

Foram executados 80 ensaios: N ∈ {3, 5, 7, 9}, dois casos e seeds de 1 a
10. O backend é o simulador `tests/sim.py`, com `FakeNetwork` e `FakeClock`;
não são medições em Docker/UDP. O código de produção de `Bully` e
`FailureDetector` é usado sem modificações.

Cada ensaio inicializa N nós, aguarda 10 segundos virtuais com acordo no
líder N, derruba esse líder e observa mais 8 segundos virtuais. A rede tem
atraso fixo de 5 ms, sem jitter ou perda aleatória. Os timeouts são 600 ms
para heartbeat, 500 ms para ANSWER e 1000 ms para COORDINATOR; heartbeats
são enviados a cada 200 ms, sem adaptação.

Para controlar quem inicia a eleição, um adaptador do experimento entrega
imediatamente ao Bully a notificação de suspeita do nó N−1 (melhor caso) ou
1 (pior caso). As notificações dos demais nós são atrasadas em 2 segundos.
O estado interno de suspeita de todos os detectores é atualizado normalmente:
isso é necessário porque a implementação ignora um coordenador menor se o
líder anterior ainda não estiver suspeito. Os candidatos intermediários
iniciam eleições ao receber ELECTION, pelo fluxo normal do algoritmo.
Este é um experimento controlado de complexidade; não representa detecções
simultâneas com a configuração homogênea padrão do Compose.

A contagem inclui todos os envios ELECTION, ANSWER e COORDINATOR após o
crash, inclusive os destinados ao nó morto. Exclui heartbeats e toda a
inicialização. Uma eleição aqui é o processo completo de substituição do
líder, incluindo as tentativas locais com diferentes election_id.

| N inicial | Melhor: ELECTION / ANSWER / COORDINATOR | Total melhor | Pior: ELECTION / ANSWER / COORDINATOR | Total pior |
|---|---|---|---|---|
| 3 | 1 / 0 / 2 | 3 | 3 / 1 / 2 | 6 |
| 5 | 1 / 0 / 4 | 5 | 10 / 6 / 4 | 20 |
| 7 | 1 / 0 / 6 | 7 | 21 / 15 / 6 | 42 |
| 9 | 1 / 0 / 8 | 9 | 36 / 28 / 8 | 72 |

Todos os ensaios terminaram com acordo em N−1 e sem handler_error. As dez
seeds de cada configuração tiveram contagens idênticas (desvio padrão zero;
p50 = p95 = p99 = média). Sem perda ou jitter, variar a seed não altera a
rede; as repetições verificam a reprodutibilidade, sem medir variabilidade
de rede ou escalonamento real.

Na implementação atual, o melhor caso envia uma ELECTION ao líder morto e
N−1 anúncios COORDINATOR (também ao morto), totalizando N: O(N).
No pior caso são N(N−1)/2 ELECTION, (N−1)(N−2)/2 ANSWER e N−1 COORDINATOR,
totalizando N(N−1): O(N²). O valor N−2 citado no planejamento corresponde
apenas aos anúncios aos outros sobreviventes no melhor caso, não a todos
os envios realizados pela implementação.

Artefatos:

- `runs.csv`: contagens por ensaio, incluindo envios ao morto e entregas.
- `summary.csv`: agregados, desvio padrão e percentis por configuração.
- `../../../images/bully-mensagens.png`: comparação das curvas, movida para
  a pasta de figuras do relatório.
- `../../raw/bully-messages-sim/`: logs JSON comprimidos, metadados por
  ensaio e identificação do ambiente/commit em `env.json`.
