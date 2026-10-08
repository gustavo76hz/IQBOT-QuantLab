"""Laboratório de padrões: varre FATIAS simples (hora x dia da semana, cor da vela
anterior) num único arquivo de velas e mostra as que se repetem fora da amostra.

    python pattern_lab.py data/iq/EURUSD-op.csv.gz --exp 5
    python pattern_lab.py --controle      (dados aleatórios: não deve achar nada)
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

import backtest as bt
import stats


def fatias(df: pd.DataFrame, exp: int, payout: float = 0.85, frac_treino: float = 0.6) -> pd.DataFrame:
    prox = df.index + pd.Timedelta(minutes=1)
    cor = np.sign(df["close"] - df["open"]).to_numpy()
    chaves = {"hora": prox.hour.to_numpy(), "dia_semana": prox.dayofweek.to_numpy(), "cor_anterior": cor}
    corte = df.index[0] + (df.index[-1] - df.index[0]) * frac_treino
    linhas = []
    for nome, val in chaves.items():
        for v in np.unique(val):
            sig = np.where(val == v, 1, 0)
            tr = bt.sem_empates(bt.simular(df, sig, exp))
            if len(tr) < 100:
                continue
            a = tr[tr["entrada_hora"] < corte]
            b = tr[tr["entrada_hora"] >= corte]
            if len(a) < 50 or len(b) < 50:
                continue
            lado = 1 if a["win"].mean() >= 0.5 else -1
            wa = a["win"] if lado == 1 else 1 - a["win"]
            wb = b["win"] if lado == 1 else 1 - b["win"]
            lo, hi = stats.wilson(int(wb.sum()), len(wb))
            linhas.append({"fatia": f"{nome}={v}", "lado": "CALL" if lado == 1 else "PUT",
                           "treino_n": len(wa), "treino_acerto": wa.mean(), "teste_n": len(wb),
                           "teste_acerto": wb.mean(), "teste_ic_baixo": lo, "teste_ic_alto": hi,
                           "p": stats.p_valor_unilateral(int(wb.sum()), len(wb), stats.breakeven(payout))})
    out = pd.DataFrame(linhas)
    if len(out):
        out["passa_bh"] = stats.benjamini_hochberg(out["p"].tolist(), 0.10)
        out["acima_breakeven"] = out["teste_ic_baixo"] > stats.breakeven(payout)
    return out.sort_values("teste_acerto", ascending=False) if len(out) else out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("arquivo", nargs="?")
    ap.add_argument("--exp", type=int, default=5)
    ap.add_argument("--payout", type=float, default=0.85)
    ap.add_argument("--controle", action="store_true")
    a = ap.parse_args()
    if a.controle or not a.arquivo:
        import sintetico
        df = sintetico.gerar(120, seed=42)
        print("CONTROLE com dados aleatórios (esperado: nenhuma fatia acima do breakeven)")
    else:
        df = pd.read_csv(a.arquivo, index_col="time", parse_dates=["time"])
        df.index = pd.to_datetime(df.index, utc=True)
    r = fatias(df, a.exp, a.payout)
    pd.set_option("display.width", 200)
    print(r.head(30).to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print(f"\nFatias testadas: {len(r)} | acima do breakeven no teste com BH: "
          f"{int((r.get('passa_bh', pd.Series(dtype=bool)) & r.get('acima_breakeven', pd.Series(dtype=bool))).sum()) if len(r) else 0}")


if __name__ == "__main__":
    main()
