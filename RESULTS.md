# Resultado Tempo de Eleição:

PRIMEIRO RESULTADO:

O experimento realizado sobre o tempo de eleição evidencia que o tempo
de eleição é aproximadamente o tempo de timeout da eleição. Esse resultado
é atingido, pois o overhead introduzido pela transmissão de mensagens é
pequena em comparação ao timeout. Nos nossos experimentos estão na ordem de
unidades de ms, enquanto o timeout está na ordem de centenas de ms.

T_{Eleição até acordo} = T_{timeout da eleição} + T_{Processamento e comunicação}.

IMAGEM: experiments/results/bully-election/election_latency_s__N.pdf

Esse resultado fica ainda mais evidente quando analisamos os resultados obtidos de:

IMAGEM: experiments/results/bully-timeout-election/election_latency_s__ELECTION_TIMEOUT_MS.png

SEGUNDO RESULTADO:

Agora podemos realizar a medição do FAILOVER_LATENCY (Tempo de resposta até volta da normalidade do sistema). Veja que ele é definido por:

T_{failover_latency} = T_{até detecção da falha} + T_{Eleição até acordo}

Contudo é observado um efeito menos linear deles. Veja que com o aumento do timeout do heartbeat há um aumento quasi-linear de recuperação do sistema.

IMAGEM: experiments/results/bully-timeout-heartbeat/failover_latency_s__HB_TIMEOUT_MS.pdf

Mesmo mantendo o tempo de timeout de eleição constante como observado em:

IMAGEM: experiments/results/bully-timeout-heartbeat/election_latency_s__HB_TIMEOUT_MS.png

Enquanto que a contribuição do timeout da eleição é linear como evidenciado pela relação de causa e efeito entre o aumento desse timeout e o tempo de recuperação do sistema como evidenciado pelas imagens:

IMAGEM 1: experiments/results/bully-timeout-election/election_latency_s__ELECTION_TIMEOUT_MS.png
IMAGEM 2: experiments/results/bully-timeout-election/failover_latency_s__ELECTION_TIMEOUT_MS.png

