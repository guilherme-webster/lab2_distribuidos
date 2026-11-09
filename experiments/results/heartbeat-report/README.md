# Resultados de heartbeat

Reprodução da consolidação: `.venv/bin/python scripts/heartbeat_report.py`.
Novas execuções: `make experiments EXP=hb-timeout` e `make experiments EXP=hb-scale`.

- Timeout: 30 ensaios novos, dez sementes por T=400/600/1000 ms; intervalo 200 ms, N=5, detector fixo, sem perdas. Cada ensaio tem 5 s de aquecimento e 28 s de observação, duas quedas com retorno. São 80 detecções por timeout, provenientes de dez execuções.
- Perda: reanálise dos heartbeats dos 40 ensaios Docker de bully-loss, dez sementes por perda, N=5, timeout 600 ms, intervalo 200 ms, 5 s de aquecimento e 30 s de observação sem crashes. Contam-se suspeitas, não eleições. A taxa do sistema é o total de suspeitas vezes 60 dividido pela duração; a taxa por par divide ainda por 20.
- Escala: 40 ensaios novos, dez sementes por N=3/5/7/9, sem perdas ou crashes; 5 s de aquecimento e 12 s de medição. Somente HEARTBEAT entra na contagem de mensagens/s.

A latência é estimada desde o último sinal de vida da vítima durante o comando Docker, não desde um instante exato de crash. Os quatro observadores da mesma queda não são repetições independentes. Os percentis usam interpolação linear; DP é amostral.

Os CSVs consolidados e provenance.json identificam as fontes. Os logs originais ficam em experiments/raw/. A figura está em ../../../images/heartbeat-resultados.pdf e .png.
