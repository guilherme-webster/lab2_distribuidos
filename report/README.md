# Relatório no Overleaf

Envie `report.tex` e a pasta `images/` da raiz do repositório para a raiz
do projeto no Overleaf. Selecione `report.tex` como documento principal e
use pdfLaTeX (classe IEEEtran). O caminho das figuras também aceita manter
o documento em `report/report.tex`, com `images/` na raiz.

As tabelas usam os CSVs existentes em `experiments/results/`: tempos por N,
varreduras de timeout, mensagens no simulador e eleições espúrias sob perda em Docker. As figuras de tempos foram
movidas dos diretórios de análise para `images/`. Os scripts de análise
podem regenerar as figuras nas suas pastas originais.

As medições Docker e simuladas estão identificadas separadamente. Os dados
de perda e escala específicos do heartbeat ainda não estão disponíveis;
o relatório explicita essa ausência. A tabela do detector usa a métrica
integrada de comando de falha até início da eleição, sem tratá-la como
latência pura do detector.

Ainda precisam ser fornecidos o link do vídeo, as fontes de código
reutilizado e as referências bibliográficas. Esses itens permanecem
marcados no documento. A paginação e a compilação final devem ser
conferidas no Overleaf; não há compilador LaTeX instalado neste ambiente.
