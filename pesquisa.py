"""Pesquisa das hipóteses (seção 9) com anos de dados externos (Dukascopy).

Passo a passo (CMD, venv ativado):
    python dukascopy.py --inicio 2020-01-01        (baixa ~6 anos; demora)
    python pesquisa.py                              (roda tudo e gera relatório)
    python pesquisa.py --controle aleatorio         (prova que não acha padrão em ruído)
    python pesquisa.py --controle plantado          (prova que acha padrão plantado)

Saídas: reports/pesquisa_<data>.html e .json; se algo for APROVADO,
data/estrategia_congelada.json (o bot usa essa regra se config.yaml mandar).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path

import pandas as pd

import report
import strategy as st
import strategy_lab as lab

RAIZ = Path(__file__).parent


def carregar_dados(pares, inicio, fim, controle, fonte="dukascopy"):
    if fonte == "iq":
        out = {}
        for arq in sorted((RAIZ / "data" / "iq").glob("*.csv.gz")):
            nome = arq.name.replace(".csv.gz", "")
            base = nome.replace("-op", "")  # EURUSD-op -> EURUSD ; OTC mantém o sufixo
            if pares and base not in pares and nome not in pares:
                continue
            df = pd.read_csv(arq, index_col="time", parse_dates=["time"])
            df.index = pd.to_datetime(df.index, utc=True)
            out[base] = df.sort_index()
            print(f"{nome}: {len(df):,} candles da IQ")
        return out
    if controle:
        import sintetico
        dias = 900 if controle == "aleatorio" else 700
        return sintetico.conjunto(4, dias, plantado=(controle == "plantado"), seed=11)
    import dukascopy
    out = {}
    for p in pares:
        try:
            out[p] = dukascopy.carregar(p, inicio, fim)
            print(f"{p}: {len(out[p]):,} candles de {out[p].index[0]:%Y-%m-%d} a {out[p].index[-1]:%Y-%m-%d}")
        except FileNotFoundError as e:
            print(f"AVISO: {e}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pares", nargs="+",
                    default=["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "EURJPY"])
    ap.add_argument("--familias", nargs="+", default=list(st.FAMILIAS))
    ap.add_argument("--exps", nargs="+", type=int, default=[1, 5, 15])
    ap.add_argument("--inicio", default=None)
    ap.add_argument("--fim", default=None)
    ap.add_argument("--meses-novos", type=int, default=6)
    ap.add_argument("--dias-novos", type=int, default=None, help="período novo em dias (dados da IQ)")
    ap.add_argument("--fonte", choices=["dukascopy", "iq"], default="dukascopy")
    ap.add_argument("--n-min-treino", type=int, default=300)
    ap.add_argument("--n-min-teste", type=int, default=200)
    ap.add_argument("--payout", type=float, default=0.85, help="payout turbo (1-5 min)")
    ap.add_argument("--payout15", type=float, default=0.80, help="payout binária 15 min")
    ap.add_argument("--controle", choices=["aleatorio", "plantado"], default=None)
    ap.add_argument("--saida", default=str(RAIZ / "reports"))
    ap.add_argument("--sem-congelar", action="store_true")
    a = ap.parse_args(argv)

    t0 = time.time()
    dados = carregar_dados(a.pares, a.inicio, a.fim, a.controle, a.fonte)
    if not dados:
        print("Sem dados. Rode primeiro: python dukascopy.py")
        return None
    c = lab.Criterios(exps=tuple(a.exps), meses_novos=a.meses_novos, dias_novos=a.dias_novos,
                      n_min_treino=a.n_min_treino, n_min_teste=a.n_min_teste,
                      payout={1: a.payout, 2: a.payout, 3: a.payout, 5: a.payout, 15: a.payout15})
    ini = min(d.index[0] for d in dados.values())
    fim = max(d.index[-1] for d in dados.values())
    per = lab.definir_periodos(ini, fim, c)
    print(f"Treino: {per['inicio']:%Y-%m-%d} a {per['corte_treino']:%Y-%m-%d} | "
          f"Teste: até {per['novo_inicio']:%Y-%m-%d} | Período novo: até {per['fim']:%Y-%m-%d}")
    closes = {k: v["close"] for k, v in dados.items()}
    todos, resumos = [], []
    for fam in a.familias:
        print(f"\n== Família: {fam} ({st.FAMILIAS[fam].descricao})")
        trades = lab.gerar_trades(dados, fam, c, closes)
        res, resumo = lab.avaliar(trades, fam, per, c)
        del trades
        print(f"   configs={resumo['testes_no_treino'] // 2} | ≠50% no treino={resumo['diferentes_de_50_no_treino']} "
              f"(por sorte ~{resumo['falsas_esperadas_treino_5pct']:.0f}) | passaram treino={resumo['passaram_treino']} "
              f"passaram teste={resumo['passaram_teste']} APROVADAS={resumo['aprovadas']}")
        for r in res:
            if hasattr(r, "_tr"):
                del r._tr
        todos += res
        resumos.append(resumo)

    aprovadas = [r for r in todos if r.status == "APROVADA"]
    wf = {}
    for r in aprovadas[:5]:
        chave = f"{r.familia} {json.dumps(r.params)} exp={r.exp} {r.ativo}"
        wf[chave] = lab.walk_forward(dados, r.familia, r.params, r.exp, r.ativo, closes_usd=closes)
    saida = Path(a.saida)
    saida.mkdir(parents=True, exist_ok=True)
    tag = (a.controle + "_") if a.controle else ("iq_" if a.fonte == "iq" else "")
    nome = f"pesquisa_{tag}{dt.datetime.now():%Y%m%d_%H%M}"
    info = {"periodos": {k: str(v) for k, v in per.items()},
            "ativos": {k: [str(v.index[0]), str(v.index[-1]), len(v)] for k, v in dados.items()},
            "criterios": {"payout": c.payout, "exps": c.exps, "n_min_treino": c.n_min_treino,
                          "n_min_teste": c.n_min_teste, "q_bh": c.q_bh},
            "controle": a.controle, "duracao_s": round(time.time() - t0)}
    (saida / f"{nome}.json").write_text(json.dumps(
        {"info": info, "resumos": resumos, "walk_forward": wf,
         "resultados": lab.resultados_para_json([r for r in todos if r.status != "reprovada no treino"])},
        indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    report.html_pesquisa(saida / f"{nome}.html", info, resumos, todos, wf, c)
    print(f"\nRelatório: {saida / (nome + '.html')}")
    if aprovadas and not a.controle and not a.sem_congelar:
        melhor = max(aprovadas, key=lambda r: r.novo["ic_baixo"] - c.be(r.exp))
        lab.congelar(melhor, RAIZ / "data" / "estrategia_congelada.json", per)
        print(f"Regra APROVADA e congelada em data/estrategia_congelada.json: {melhor.familia} {melhor.params}")
    elif not a.controle:
        print("\nNENHUMA hipótese passou em todas as etapas. Recomendação: não operar com dinheiro real.")
    print(f"Tempo total: {time.time() - t0:.0f}s")
    return {"resumos": resumos, "aprovadas": aprovadas, "todos": todos}


if __name__ == "__main__":
    main()
