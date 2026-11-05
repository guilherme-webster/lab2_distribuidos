# Rascunhos de experimentos

Os arquivos `bully-messages-best.toml` e `bully-messages-worst.toml` preservam
a proposta de contar mensagens com N=3/5/7/9 e dez repetições, comparando
o segundo maior ID sobrevivente com o menor ID como iniciador.

Eles ainda não são executáveis: faltam o analisador `bully_messages` e o
controle do iniciador no executor. Os campos `election_initiator` e
`passive_hb_timeout_ms` representam uma proposta, sem suporte no código atual.

A ideia de atrasar a detecção dos demais nós precisa ser revista: enquanto
eles confiam no líder antigo, podem rejeitar o anúncio de um sucessor menor.
O controle deve permitir que todos detectem a falha, restringindo apenas
quem inicia a eleição por suspeita, sem impedir respostas a `ELECTION`.

Esta pasta fica fora da descoberta automática de `make experiments`.
Mova as configurações para `experiments/configs/` depois de implementar e
testar o suporte. Nenhum dado do experimento de mensagens foi coletado.
