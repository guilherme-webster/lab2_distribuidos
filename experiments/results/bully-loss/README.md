# Eleições espúrias sob perda

40 ensaios Docker concluídos: cinco nós, dez sementes para cada perda de
0%, 5%, 10% e 20%. Aquecimento de 5 s; observação de 30 s. Nenhum crash
ou pausa foi injetado. Todas as métricas de integridade do experimento
registraram zero violações.

`runs.csv` contém contagens e motivos por ensaio; `summary.csv` contém
média, DP amostral e percentis. As contagens são tentativas locais
(nó, election_id), não rodadas globais. `spurious_election_rate` é a taxa
por minuto do sistema inteiro, sem dividir por cinco. Startup está excluído.

O gráfico incorporado ao relatório foi movido para
`../../../images/bully-perdas.pdf`. Os PNGs e o gráfico da taxa normalizada
permanecem aqui; a análise pode regenerar o PDF original.

Protocolo detalhado: `../../configs/bully-loss.md`.
Reprodução: `make experiments EXP=bully-loss`.
