# Eleições espúrias sob perda

Execute `make experiments EXP=bully-loss` para executar e analisar os ensaios.
O executor usa o projeto Docker isolado `mc714-exp`; não execute outro
experimento com esse mesmo projeto enquanto a varredura estiver em andamento.

São 5 nós, 10 sementes por configuração, 5 segundos de aquecimento e 30
segundos de observação, com perdas de 0%, 5%, 10% e 20%. A perda é aplicada
a todos os tipos de mensagem, inclusive heartbeats e mensagens do Bully.
Não há crash, pausa, recuperação, atraso ou jitter artificial. O detector
usa timeout fixo de 600 ms, heartbeat de 200 ms, espera por ANSWER de 500 ms
e por COORDINATOR de 1000 ms.

`spurious_elections` conta cada par (nó, election_id) com evento
`election_start` dentro da janela [início, fim), excluindo `reason=startup`.
Uma tentativa propagada ou repetida é contada separadamente. Esta métrica
é um total de tentativas locais no sistema, não uma quantidade de rodadas
globais: não existe um identificador global de rodada na implementação.
`spurious_election_rate` normaliza essa contagem por minuto do sistema,
sem dividir pelo número de nós. As contagens por motivo distinguem os
gatilhos por suspeita de líder das propagações e repetições.

O analisador verifica que todos os nós iniciaram antes da observação e
continuaram emitindo mensagens, sem falhas injetadas, reinícios, paradas
ou erros de handler. Um ensaio que viole essas condições não fornece
contagem válida ao agregado. Acordo no início/fim é informado separadamente:
divergência sob perda é parte do comportamento observado, não motivo para
excluir um ensaio.

O relatório usa média, desvio-padrão amostral e percentis com interpolação
linear entre as dez contagens de cada configuração. Logs, metadados,
ambiente e especificação ficam em `experiments/raw/bully-loss/`; CSVs e
gráficos são produzidos em `experiments/results/bully-loss/`.
