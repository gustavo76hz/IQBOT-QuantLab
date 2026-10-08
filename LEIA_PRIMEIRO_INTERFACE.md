# IQBOT · Quant Lab — interface do bot

A interface só **liga e desliga** o mesmo bot do `main.py`. Estratégia, stop, notícias e limites
são exatamente os mesmos. Abrir a interface **não** inicia operações.

## Como abrir (sem janela do CMD)

O `instalar.py` cria o ícone **IQBOT Quant Lab** na Área de Trabalho. Se precisar criar à mão
(botão direito → Novo → Atalho), use este destino:

```
"C:\caminho\para\iqbot-quant-lab\venv\Scripts\python.exe" "C:\caminho\para\iqbot-quant-lab\app.py" --oculto
```

Em Propriedades do atalho: **Iniciar em** = `C:\caminho\para\iqbot-quant-lab`
e **Alterar Ícone** → `interface\assets\anjo.ico`.

O `--oculto` faz o app reabrir sem a janela do CMD. O atalho usa o `python.exe` (e não o
`pythonw.exe`) porque o Controle Inteligente de Aplicativos do Windows 11 pode bloquear o
`pythonw.exe`. Se a IQ pedir o código 2FA, ele aparece numa janelinha dentro do próprio app.
Se a interface não abrir, o motivo fica em `logs\interface_erro.log`.

Pelo CMD (mostra mensagens técnicas): `venv\Scripts\python.exe app.py`

Se a janela não abrir: `venv\Scripts\python.exe app.py --browser` (abre no navegador).

## Como usar

1. Em **Conta**, escolha: Simulação (sem dinheiro), Demo ou Real.
2. Clique em **Iniciar**. Na conta Real, digite a palavra de confirmação (`CONFIRMO`).
3. Para parar, clique em **Parar**: ele não abre novas operações e espera as abertas terminarem.

## O que aparece na tela

| Parte | O que mostra |
|---|---|
| Faixa colorida no topo | O que o bot está fazendo agora, em português simples. Verde = operando; amarelo = esperando ou pausado (ex.: notícia, pares fechados); vermelho = parado pelo stop |
| Cartões | Saldo, resultado de hoje, operações de hoje (ganhas, perdidas, acerto) e perdas seguidas (o stop para o bot em 2) |
| Pares | Os pares do config.yaml: aberto ou fechado na IQ, payout e o último sinal. Clique em um para ver o gráfico |
| Últimas operações | As 15 mais recentes da conta escolhida |
| Registro técnico | O log do bot (fica fechado; clique para abrir) |

Com o bot parado, trocar a **Conta** mostra o histórico e o resultado de hoje daquela conta.

## Segurança

- **Não rode a interface e o `main.py` ao mesmo tempo.** Agora os dois se bloqueiam: se um já
  estiver rodando, o outro avisa e não inicia (trava em `data\bot.lock`).
- O arquivo `STOP` continua valendo: com ele na pasta, o bot não inicia.
- A interface continua gravando o `logs\bot.log` e o banco `data\bot.db`, como o `main.py`.
- A simulação usa um banco separado (`data\interface_simulacao.db`), zerado a cada simulação.
- Telegram fica desligado na interface.
- Fechar a janela com o bot rodando pede para parar primeiro; feche de novo depois que parar.
