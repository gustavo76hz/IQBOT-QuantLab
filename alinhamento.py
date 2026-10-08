"""Mede o ALINHAMENTO entre a Dukascopy (usada na pesquisa) e os candles -op da IQ
(que liquidam a opção). Rode depois de `python edge_finder.py --baixar` e de baixar a
Dukascopy cobrindo as mesmas datas.

    python alinhamento.py

O número mais importante é a CONCORDÂNCIA DE DIREÇÃO em 1 e 5 minutos: se for 90%,
uma vantagem de 58% medida na Dukascopy vira algo como 0,9*58% + 0,1*42% ≈ 56,4% na IQ.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import dukascopy

RAIZ = Path(__file__).parent


def comparar(iq: pd.DataFrame, dk: pd.DataFrame) -> dict:
    j = iq[["close"]].join(dk[["close"]], how="inner", lsuffix="_iq", rsuffix="_dk").dropna()
    if len(j) < 500:
        return {"n": len(j)}
    out = {"n": len(j), "de": str(j.index[0]), "ate": str(j.index[-1])}
    dif = j["close_iq"] - j["close_dk"]
    escala = 100 if j["close_iq"].mean() > 20 else 10000  # pips
    out["dif_mediana_pips"] = float(dif.median() * escala)
    out["dif_abs_p95_pips"] = float(dif.abs().quantile(0.95) * escala)
    for k in (1, 5):
        ri = j["close_iq"].diff(k)
        rd = j["close_dk"].diff(k)
        m = (ri != 0) & (rd != 0) & ri.notna() & rd.notna()
        out[f"corr_retornos_{k}m"] = float(np.corrcoef(ri[m], rd[m])[0, 1])
        out[f"concorda_direcao_{k}m"] = float((np.sign(ri[m]) == np.sign(rd[m])).mean())
    return out


def main():
    for arq in sorted((RAIZ / "data" / "iq").glob("*-op.csv.gz")):
        nome = arq.name.replace(".csv.gz", "")
        par = nome.replace("-op", "")
        iq = pd.read_csv(arq, index_col="time", parse_dates=["time"])
        iq.index = pd.to_datetime(iq.index, utc=True)
        try:
            dk = dukascopy.carregar(par, str(iq.index[0].date()))
        except FileNotFoundError:
            print(f"{nome}: sem Dukascopy de {par} (rode dukascopy.py --pares {par} --inicio {iq.index[0]:%Y-%m-%d})")
            continue
        r = comparar(iq, dk)
        if r["n"] < 500:
            print(f"{nome}: poucas velas em comum ({r['n']})")
            continue
        print(f"{nome}: n={r['n']:,} | diferença mediana {r['dif_mediana_pips']:+.1f} pips (p95 {r['dif_abs_p95_pips']:.1f}) | "
              f"correlação 1m {r['corr_retornos_1m']:.3f} 5m {r['corr_retornos_5m']:.3f} | "
              f"direção igual 1m {r['concorda_direcao_1m']:.1%} 5m {r['concorda_direcao_5m']:.1%}")


if __name__ == "__main__":
    main()
