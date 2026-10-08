# Automação de pares e estratégias — como funciona

O bot agora decide sozinho **onde** operar (pares de mercado ou OTC) olhando o que a IQ Option tem
aberto de verdade, e avalia com dados **qual estratégia** está aprovada. Você não precisa mais pedir
para trocar de estratégia às 18h ou de manhã.

## O que ele faz sozinho

1. **Detecta a fase do mercado** a cada 3 minutos:
   - **MERCADO**: só pares de mercado abertos;
   - **MISTO**: pares de mercado e OTC abertos (o normal durante o dia);
   - **OTC**: só OTC (noite, fim de semana ou quando a IQ suspende os pares de mercado);
   - **FECHADO**: nada aberto.

   Ele só muda de fase depois de **2 leituras seguidas iguais**, para não pular por causa de uma
   suspensão de 1 minuto.
2. **Roda o plano certo para a fase.** Existe um plano para pares de mercado e outro para OTC. No
   MISTO roda o de mercado. Se o de mercado não puder operar (por exemplo, a reversão só opera das
   9h às 18h), o do OTC assume.
3. **Reavalia as estratégias 1 vez por dia**, sem atrapalhar o bot. Baixa as velas que faltam,
   testa cada estratégia em cada par nos últimos 30 dias e aplica as regras abaixo. O resultado fica
   em `data\avaliacao.json`.
4. **Grava o humor** de todos os pares, como antes, para um dia validar a tática do humor.

## O que pede a sua aprovação (modo `semi`, o padrão)

- **Trocar de estratégia.** Quando a avaliação acha uma estratégia aprovada melhor que a atual,
  aparece um cartão **Recomendação** no Painel com os números e os botões **Aplicar** / **Ignorar**.
  Ignorar esconde a recomendação por 24 h. Na conta Real, aplicar pede a palavra `CONFIRMO`.
- A troca só acontece **sem operação aberta**. O stop de 2 perdas, os limites do dia e o orçamento
  **continuam exatamente iguais**: nada é zerado.
- Com uma estratégia **aprovada**, os pares dela seguem a avaliação sozinhos (só os pares aprovados).
  Com uma estratégia **em teste** ou **não aprovada**, ele usa os pares da sua lista (`ativos.lista`).

## Regras para aprovar uma estratégia num par (fixas, definidas antes de ver os resultados)

- pelo menos **300 operações** simuladas em 30 dias;
- resultado **significativo** mesmo descontando que são testadas centenas de combinações
  (correção de Benjamini-Hochberg);
- pelo menos **51,5% em cada metade** do período (precisa ser estável);
- últimos 7 dias **sem desabar** (pelo menos 50%);
- no máximo **2 pontos abaixo do empate** com o payout atual (com payout de 85%, o empate é 54,1%,
  então o mínimo é cerca de 52,1%).

Aprovada **não quer dizer que dá lucro**: quer dizer que o efeito medido é real e estável o bastante
para valer o teste. A aba **Automação** mostra os números de cada par (acerto, intervalo de confiança,
quantidade de operações) e por que cada um ficou de fora.

## Estratégias "em teste"

O **humor** (contra a multidão) não dá para testar no histórico da IQ, então fica **EM TESTE**.
Como você escolheu, ele continua operando no OTC à noite e no fim de semana, com o aviso "em teste".
Em dia útil, das 9h às 18h, uma estratégia do OTC que não está aprovada **só grava dados**: é a regra
criada depois das 2 perdas de 07/10. Uma estratégia de OTC **aprovada** opera em qualquer horário.

## A aba Automação

- fase do mercado e quantos pares estão abertos;
- avaliação: data da última, regras e o botão **Reavaliar agora** com barra de progresso (o bot
  continua operando enquanto isso);
- os dois planos (mercado e OTC): estratégia, situação (aprovada, em teste, não aprovada), se está
  rodando agora, os pares e um seletor para trocar de estratégia na mão;
- os pares avaliados, com os números e o motivo de cada reprovação;
- o resultado **ao vivo** por estratégia e mercado (sempre com quantidade e intervalo de confiança);
- o histórico de trocas (quando, de → para, e quem trocou: você ou o automático). Esse histórico também
  fica no banco, na tabela `trocas_estrategia`, e no `logs\bot.log`.

## Modos (config.yaml → `automacao.modo`)

| Modo | Fase do mercado | Pares | Estratégia |
|---|---|---|---|
| `manual` | segue sozinho | os da sua lista | só muda quando você aplica |
| `semi` (padrão) | segue sozinho | os aprovados (se a estratégia é aprovada) | só muda quando você aplica |
| `auto` | segue sozinho | os aprovados | troca sozinho para a melhor **aprovada** |

## Voltar ao funcionamento antigo

No `config.yaml`, coloque `automacao:` → `ativo: false` e reinicie o bot. Ele volta a usar a seção
`estrategia` como antes.

## Arquivos novos

- `mercado.py`: detector de fase;
- `catalogo.py`: as estratégias que o bot conhece;
- `avaliador.py`: o backtest e as regras de aprovação. Também roda sozinho com
  `python avaliador.py --calcular`;
- `seletor.py`: os planos, os modos, as recomendações e as trocas;
- `data\automacao.json`: plano atual e trocas pendentes;
- `data\avaliacao.json`: resultado da última avaliação;
- `data\payouts.json`: payouts atuais.

A simulação usa a pasta separada `data\simulacao`, que não mistura dados falsos com os reais.
