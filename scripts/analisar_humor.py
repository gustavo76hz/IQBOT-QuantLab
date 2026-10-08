"""Valida a tática 'contra a multidão' com o humor GRAVADO pelo bot.

Passo a passo (bot desligado):
    python edge_finder.py --baixar --todos-otc      (velas atualizadas dos pares OTC)
    python scripts\\analisar_humor.py

Para cada limite (60%/70%/80%) e expiração (1/2/3/5 min) mostra o acerto de operar
CONTRA a maioria, separado em 1ª metade / 2ª metade do período gravado.
"""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import stats  # noqa: E402

arqs = sorted((RAIZ / "data" / "humor").glob("humor_*.csv"))
if not arqs:
    sys.exit("Sem humor gravado ainda. Rode o bot com a tática 'humor' por algumas horas.")
h = pd.concat(pd.read_csv(a) for a in arqs)
h["time"] = pd.to_datetime(h["time_utc"], utc=True)
print(f"Humor gravado: {len(h):,} leituras, {h.ativo.nunique()} pares, de {h.time.min():%d/%m %H:%M} a {h.time.max():%d/%m %H:%M} UTC")
linhas = []
for ativo, g in h.groupby("ativo"):
    arq = RAIZ / "data" / "iq" / f"{ativo}.csv.gz"
    if not arq.exists():
        continue
    d = pd.read_csv(arq, index_col="time", parse_dates=["time"])
    d.index = pd.to_datetime(d.index, utc=True)
    g = g.drop_duplicates("time").set_index("time")
    for exp in (1, 2, 3, 5):
        ent = d["open"].reindex(g.index)                       # entrada: abertura da vela do minuto
        sai = d["close"].reindex(g.index + pd.Timedelta(minutes=exp - 1))  # saída: fechamento após exp min
        mov = np.sign(sai.to_numpy() - ent.to_numpy())
        linhas.append(pd.DataFrame({"ativo": ativo, "exp": exp, "time": g.index,
                                    "pct": g["pct_call"].to_numpy(), "mov": mov}))
if not linhas:
    sys.exit("Sem velas dos pares gravados. Rode: python edge_finder.py --baixar --todos-otc")
T = pd.concat(linhas).dropna()
T = T[T.mov != 0]
meio = T.time.min() + (T.time.max() - T.time.min()) / 2
print(f"\n{'limite':>6s} {'exp':>4s} {'n':>6s} {'acerto':>7s} {'IC95%':>14s} {'1ª metade':>10s} {'2ª metade':>10s}")
for lim in (0.6, 0.65, 0.7, 0.75, 0.8):
    for exp in (1, 2, 3, 5):
        x = T[(T.exp == exp) & ((T.pct >= lim) | (T.pct <= 1 - lim))].copy()
        if len(x) < 30:
            continue
        x["win"] = (x.mov == np.where(x.pct >= lim, -1, 1)).astype(int)   # contra a maioria
        lo, hi = stats.wilson(int(x.win.sum()), len(x))
        a, b = x[x.time < meio].win.mean(), x[x.time >= meio].win.mean()
        print(f"{lim:6.0%} {exp:4d} {len(x):6d} {x.win.mean():7.1%}  [{lo:5.1%},{hi:5.1%}] {a:10.1%} {b:10.1%}")
print(f"\nMínimo para lucrar com payout 85%: {stats.breakeven(0.85):.1%}. Só confie se as DUAS metades passarem disso.")
print("Obs.: se o acerto ficar bem ABAIXO de 50%, o sinal é útil ao contrário (seguir a multidão).")
