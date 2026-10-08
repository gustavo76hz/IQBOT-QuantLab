"""Compara o resultado AO VIVO de cada tática/estratégia já usada no bot.
python scripts/comparar_taticas.py"""
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import pandas as pd  # noqa: E402

import stats  # noqa: E402

con = sqlite3.connect(RAIZ / "data" / "bot.db")
o = pd.read_sql("select * from operacoes", con)
con.close()
o = o[o["resultado"].isin(["win", "loose"])].copy()
if o.empty:
    print("Ainda não há operações fechadas.")
    sys.exit(0)
o["win"] = (o["resultado"] == "win").astype(int)
o["dia"] = (pd.to_datetime(o["aberta_em"], utc=True) - pd.Timedelta(hours=3)).dt.date
o["tatica"] = o["estrategia"].str.replace(r'.*"tatica": "([a-z0-9_]+)".*', r"\1", regex=True)
o.loc[~o["estrategia"].str.contains("tatica"), "tatica"] = o["estrategia"].str.replace("regime:", "", regex=False)
print(f"{"tática":62s} {'n':>4s} {'acerto':>7s}  {'IC95%':>13s} {'lucro':>8s}  dias")
for t, g in o.groupby("tatica"):
    lo, hi = stats.wilson(int(g.win.sum()), len(g))
    print(f"{t:62s} {len(g):4d} {g.win.mean():7.1%}  [{lo:4.0%},{hi:4.0%}] {g.lucro.sum():+8.2f}  {g.dia.nunique()}")
print("\nPor dia e tática:")
print(o.groupby(["dia", "tatica"]).agg(n=("win", "size"), acerto=("win", "mean"), lucro=("lucro", "sum"))
      .round(2).to_string())
pay = o["payout"].mean()
print(f"\nMínimo para lucrar com payout médio {pay:.0%}: {stats.breakeven(pay):.1%}")
