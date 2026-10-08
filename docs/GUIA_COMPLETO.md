# Bot IQ Option (opções turbo) com validação estatística

Bot em Python para opções binárias turbo (1–5 min) e binárias de 15 min da IQ Option. O foco é **medir antes de acreditar**. Ele já traz:

- um pipeline de pesquisa com anos de dados externos (Dukascopy);
- os controles obrigatórios (ruído e padrão plantado);
- um loop ao vivo que usa a **mesma função de decisão** do backtest (isso é testado);
- gestão de risco com stop diário, teto de perda total, kill switch e um monitor que **desliga o bot** quando o acerto ao vivo fica comprovadamente abaixo do breakeven.

> ## Leia antes de usar dinheiro real
>
> - **Até hoje nenhuma estratégia mostrou acerto acima do breakeven fora da amostra.** Tudo o que foi medido ficou em ~50% (veja "Resultados já medidos"). O breakeven é de 53–55%.
> - Com 50% de acerto e payout de 87%, cada entrada perde em média **6,5%** do valor apostado. Com entrada de 5, são cerca de 32 de prejuízo a cada 100 operações. Com payout de 85%, a perda média sobe para 7,5%.
> - **Dois dias de dados não provam nada.** Exemplo: 25 acertos em 40 operações (62,5%) têm IC95% de 47% a 76%, o que é compatível com pura sorte. Para provar um acerto de 58% são precisas cerca de **500 operações**; para 56%, cerca de 1.500.
> - A IQ Option **não é autorizada pela CVM** a oferecer esses produtos no Brasil, então não há proteção regulatória.
> - Na conta REAL o bot exige uma frase de confirmação e respeita o `orcamento_teste`, que é um teto de perda total. Ele **nunca promete lucro**.

---

## 1. Instalação no Windows (passo a passo para iniciante)

1. Instale o **Python 3.11** (python.org) e marque **"Add python.exe to PATH"**. O 3.13 também funciona; use o 3.11 se aparecer erro de compatibilidade.
2. Descompacte este projeto, por exemplo em `C:\bot_iq`.
3. Dê **dois cliques em `instalar.py`**. Ele cria o venv, instala tudo (inclusive a iqoptionapi pelo ZIP, sem precisar de Git) e cria o ícone na Área de Trabalho. Não há arquivos `.bat`: o Windows 11 com o Controle Inteligente de Aplicativos os bloqueia.
   Se preferir fazer à mão no CMD, rode um comando por linha:
   ```
   cd C:\bot_iq
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   pip install -U https://github.com/iqoptionapi/iqoptionapi/archive/refs/heads/master.zip
   pip install websocket-client==0.56.0
   copy .env.exemplo .env
   copy config.exemplo.yaml config.yaml
   ```
4. Abra o **`.env`** no Bloco de Notas ou no VS Code e coloque seu e-mail e sua senha da IQ. Esse arquivo **nunca** vai para o GitHub (o `.gitignore` já cuida disso).
5. Revise o **`config.yaml`**, principalmente a seção `risco:`. **Confira no app da IQ qual é a entrada mínima** da sua moeda.

## 2. Ordem recomendada

| Passo | Comando (CMD com o venv ativado) | Para quê |
|---|---|---|
| 1 | `python scripts\diagnostico.py` | confere Python, pacotes, .env, config e internet |
| 2 | `python -m pytest -q` | roda os 40 testes (corretora falsa, lookahead, controles) |
| 3 | `python main.py --simular` | roda o loop inteiro sem conta e sem internet |
| 4 | `python scripts\test_connection.py` | login na DEMO, saldo, ativos e velas |
| 5 | `python scripts\verificar_pares.py` | **obrigatório:** compare os preços com o app |
| 6 | `python dukascopy.py --inicio 2020-01-01` | baixa ~6 anos de M1 (1–3 h na 1ª vez) |
| 7 | `python pesquisa.py --controle aleatorio` e `--controle plantado` | prova que a pesquisa funciona |
| 8 | `python pesquisa.py` | testa as hipóteses e gera `reports\pesquisa_*.html` |
| 9 | `python edge_finder.py --baixar` e depois `python alinhamento.py` | mede se a Dukascopy acompanha a IQ |
| 10 | `python main.py` | opera (REAL pede a frase de confirmação) |

Para parar com segurança: crie um arquivo chamado **`STOP`** na pasta do bot (ou use o botão Parar no app). Ctrl+C também funciona. Quando o bot fecha, ele gera `reports\operacoes.html`.

## 3. Como a "precisão" é medida

- **breakeven = 1/(1+payout)**. Com payout de 87% → 53,5%; com 85% → 54,1%; com 80% → 55,6%.
- **Expectativa por entrada = acerto × (1+payout) − 1.**
- **Backtest:** a decisão sai no fechamento da vela t, a entrada é no **open de t+1** e o vencimento no **close de t+E**. Não há sobreposição de operações, e operações que atravessam fim de semana são descartadas. Na expiração de 15 min, a entrada só acontece no quarto de hora (é assim que a lib calcula o vencimento).
- **Sem lookahead:** um teste automático recalcula todas as features com e sem os dados futuros e exige valores idênticos. Outro teste compara a decisão "ao vivo" com a do backtest, vela a vela.
- **Loop = backtest:** `tests/test_equivalencia_loop.py` roda o bot completo contra a corretora falsa e exige **exatamente** as mesmas entradas, direções e resultados do backtest.
- **Pipeline (`strategy_lab.py`):**
  1. Os dados são divididos no tempo em **treino 60% | teste 40% | período novo (últimos 6 meses, intocado)**.
  2. No treino rodam todas as combinações de parâmetro, expiração e ativo, e o **lado** (seguir ou inverter) é escolhido ali. O relatório mostra quantos testes foram feitos e quantos dariam "significativo" só por sorte.
  3. Para ir ao teste, a combinação precisa ter o limite inferior do IC95% (Wilson) acima do breakeven, com n ≥ 300.
  4. No teste, a regra fica congelada e precisa passar em tudo: IC95% inferior acima do breakeven, Benjamini-Hochberg, **bootstrap por DIA**, continuar acima do breakeven **sem os 5% de dias mais concentrados**, ficar acima do breakeven em ≥75% dos blocos cronológicos e na maioria dos ativos.
  5. No **período novo**, ainda congelada, o IC95% inferior tem que ficar acima do breakeven. Só aí ela vira "APROVADA" e é gravada em `data/estrategia_congelada.json`, que o bot passa a usar sozinho (com `fonte: auto`).
  6. As aprovadas passam ainda por um walk-forward (treino de 12 meses, teste de 3 meses, janela móvel).
- **Controles:** com ruído, o pipeline **não aprova nada**; com um padrão plantado (deriva às 14h UTC), ele **acha exatamente esse padrão** (hora 14, CALL). Os dois são testes automáticos.

### Resultado dos controles (rodados no desenvolvimento)

| Controle | O que aconteceu |
|---|---|
| Ruído (4 ativos, 900 dias, 6 famílias, 1.083 combinações) | 30 deram "≠50%" no treino, quando ~53 eram esperadas só por sorte. 0 passaram no treino e **0 aprovadas** ✔ |
| Padrão plantado na hora 14 | as **únicas** aprovadas foram "hora=14, CALL" (54,7% em 1 min; 59–69% em 5 e 15 min). Todas as outras das 839 combinações (famílias hora e regime) foram reprovadas ✔ |

## 4. Hipóteses que a pesquisa testa (seção 9)

| Família | Ideia |
|---|---|
| `hora` | direção fixa por hora do dia (sessões), com anos de dados |
| `regime` | momentum ou reversão depois de um movimento forte (5/15/60 min), separado por regime de volatilidade (ATR curto ÷ ATR de 1 dia) |
| `choque` | candle de choque (>4σ ou >6σ), com ou sem restrição a :00/:30, que é quando saem as notícias. Proxy de "deriva após notícia" |
| `dxy` | **índice do dólar sintético** (média de EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF): o dólar se move e o par ainda não acompanhou |
| `bollinger`, `sequencia` | controles históricos (já falharam na IQ) |
| expiração 15 min | todas as famílias rodam em 1, 5 e 15 min (`--exps`) |

Ainda **não testável**: a reação a notícias com o calendário de verdade, porque o feed do Forex Factory só traz a semana atual. Por isso o bot **salva o calendário toda semana** em `data/noticias/`; com alguns meses salvos, dá para testar.

## 5. Resultados já medidos antes deste projeto (não repetir)

- Payouts: pares -op de 85–88% (breakeven 53,2–54,1%); OTC de 82–86% (54–55%).
- Confluência RSI/Stoch/MACD/Bollinger/EMAs/ADX em 4 pares e 40 dias: 48,8–50,4% de acerto. Exigir mais condições **não** aumentou o acerto.
- OTC (7.664 sinais) e pares normais (10.478 sinais): ~50%. Nenhuma fatia se confirmou fora da amostra.
- Reversão após candle gigante: 56–60% no período antigo e 46–50% no recente. Reversão em Bollinger 2,5–3σ: 55–56% no treino e 50–52% no teste. **Padrão comum: ideias de reversão parecem boas no passado e somem.**

## 6. O que o bot faz ao vivo (`main.py`)

A cada vela de 1 minuto (1,5 s depois da abertura), o bot:

1. confere o kill switch, o stop/meta do dia, o teto de perda total, os limites por hora e por dia, e se está em pausa por perdas seguidas;
2. **monitor:** se, com ≥40 operações, o IC95% do acerto ao vivo ficar **inteiro abaixo do breakeven**, o bot para e avisa;
3. a cada 15 minutos dá nota de 0 a 100 aos ativos (payout, regime de volatilidade, qualidade da cotação, validação) e acompanha os melhores;
4. busca só as velas novas (cache), confere se o dado está fresco e aplica a **mesma** `strategy.decidir_agora()` do backtest;
5. bloqueia o sinal se houver notícia de alto impacto na moeda (±15 min) ou payout abaixo do mínimo. Sinais bloqueados também ficam no banco;
6. envia a ordem, grava no SQLite e acompanha o resultado em segundo plano (polling em `socket_option_closed`).

O painel (rich) mostra conta, saldo, resultado do dia, orçamento restante, estratégia (VALIDADA ou NÃO) e as últimas operações, ajustado à altura da janela.

### Martingale (desligado por padrão)
Com entrada 5, fator 2,2 e 2 gales, as entradas ficam em 5 → 11 → 24,20. Perder a sequência custa **40,20** (8 entradas); ganhar rende ~4,35. Isso **não cria vantagem**: troca muitos ganhos pequenos por poucas perdas grandes. O bot mostra essa conta quando o martingale está ligado.

## 7. Critério para dinheiro real (o que o projeto recomenda)
O limite inferior do IC95% **fora da amostra** tem que estar acima do breakeven, **e** isso tem que se repetir no período novo, **e** o alinhamento Dukascopy × IQ tem que ser alto. Enquanto isso não acontecer, o mais prudente é a conta PRACTICE. Se mesmo assim usar a REAL, use a entrada mínima e um `orcamento_teste` que você aceite perder inteiro.

## 8. Estrutura

```
main.py            loop ao vivo (e --simular)       connection.py   TODA a iqoptionapi isolada aqui
strategy.py        famílias/regras (backtest=vivo)  backtest.py     simulação sem lookahead
strategy_lab.py    pipeline de validação            pesquisa.py     roda a seção 9 com Dukascopy
edge_finder.py     baixa/valida com dados da IQ     pattern_lab.py  varredura de fatias simples
dukascopy.py       download de anos de M1           alinhamento.py  Dukascopy × IQ
stats.py           Wilson, bootstrap por dia, BH    indicators.py   RSI, ATR, Bollinger, ADX...
risk.py            risco/martingale/kill switch     monitor.py      invalidação ao vivo
executor.py        envia e acompanha ordens         database.py     SQLite (fecha a conexão sempre)
asset_selector.py  nota 0-100                       news.py         calendário Forex Factory
notifier.py        Telegram                         dashboard.py    painel rich
report.py          CSV/HTML                         fake_broker.py  corretora FALSA + relógios
sintetico.py       dados para os controles          configuracao.py lê/valida o config.yaml
scripts/           test_connection, verificar_pares, diagnostico, zerar_historico (arquiva)
tests/             40 testes (não dependem do seu config.yaml)
```

O projeto **não usa scikit-learn** (o Smart App Control do Windows pode bloquear os `.pyd` dele). Tudo é numpy/pandas.

## 9. Particularidades da iqoptionapi 7.1.1 (já tratadas em `connection.py`)

1. `get_all_open_time()` trava no mercado digital, então o código usa `get_all_init_v2()["turbo"]["actives"]`.
2. Os ids dos ativos `-op` são registrados em `constants.ACTIVES` antes de `get_candles`/`buy`. Sem isso aparece "Asset X not found on consts".
3. O payout é calculado como `(100 − commission)/100`, a partir do próprio init v2.
4. O resultado vem por polling em `api.api.socket_option_closed[id]["msg"]` com timeout; `check_win_v3` fica só como último recurso.
5. Toda chamada roda em thread com timeout, e a reconexão usa espera crescente (5 s → 300 s).
6. **Não abra o app da IQ nem outro script na mesma conta** enquanto o bot roda: isso derruba a sessão.

## 10. Erros comuns

- **"Asset X not found on consts"**: o ativo não está na lista turbo agora. Rode `verificar_pares.py`.
- **2FA**: o bot pede o código no CMD.
- **Timeout ao conectar**: feche o app/navegador logado na IQ, espere alguns minutos e tente de novo.
- **`pytest` bloqueado pelo Smart App Control**: em geral é o scikit-learn de outro projeto. Este projeto não precisa dele.
- **Arquivo `.db` "em uso"**: feche o bot antes de rodar `zerar_historico.py`.

## Fontes
- Biblioteca: github.com/iqoptionapi/iqoptionapi (documentação: lu-yi-hsun.github.io/iqoptionapi)
- Dados M1: datafeed público da Dukascopy (uso pessoal/pesquisa; confira os termos em dukascopy.com)
- Calendário: nfs.faireconomy.media/ff_calendar_thisweek.json (Forex Factory)
- Telegram Bot API: core.telegram.org/bots/api
