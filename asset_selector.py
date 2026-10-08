"""Nota 0-100 para cada ativo. Não prevê nada: só escolhe onde é MENOS ruim operar.

  payout     (0-50): 75% -> 0 pts, 92% -> 50 pts (payout maior = breakeven menor)
  volatilidade(0-20): regime nem morto nem explosivo (vol_ratio perto de 1)
  qualidade  (0-15): poucas velas paradas (preço igual = empate/ruído de cotação)
  validação  (0-15): o ativo faz parte da regra aprovada na pesquisa
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import indicators as ind


def nota(ativo: str, payout: float, df: pd.DataFrame | None, ativo_validado: str | None) -> tuple[float, dict]:
    det = {}
    det["payout"] = float(np.clip((payout - 0.75) / (0.92 - 0.75), 0, 1) * 50)
    if df is not None and len(df) > 300:
        a14 = ind.atr(df, 14).iloc[-1]
        a_l = ind.true_range(df).ewm(span=1440, adjust=False, min_periods=200).mean().iloc[-1]
        vr = float(a14 / a_l) if a_l and np.isfinite(a_l) else 1.0
        det["volatilidade"] = float(np.clip(1 - abs(np.log(max(vr, 1e-6))) / np.log(3), 0, 1) * 20)
        paradas = float((df["close"].tail(300).diff() == 0).mean())
        det["qualidade"] = float(np.clip(1 - paradas / 0.3, 0, 1) * 15)
    else:
        det["volatilidade"] = 0.0
        det["qualidade"] = 0.0
    base = ativo.split("-")[0].upper()
    det["validacao"] = 15.0 if ativo_validado and ativo_validado.upper() in ("TODOS", base) else 0.0
    return round(sum(det.values()), 1), det


def escolher(candidatos: dict[str, float], dfs: dict[str, pd.DataFrame], cfg_ativos: dict,
             ativo_validado: str | None) -> list[tuple[str, float, dict]]:
    lista = []
    for a, pay in candidatos.items():
        if pay < cfg_ativos["payout_minimo"]:
            continue
        n, d = nota(a, pay, dfs.get(a), ativo_validado)
        d["payout_valor"] = pay
        if n >= cfg_ativos["nota_minima"]:
            lista.append((a, n, d))
    # Pares de mercado (-op) primeiro: neles a reversão tem efeito medido (~52,5%);
    # no OTC (preço gerado pela corretora) o acerto medido é ~50%. OTC só completa as vagas.
    lista.sort(key=lambda x: (x[0].endswith("-OTC"), -x[1]))
    return lista[: cfg_ativos["max_ativos_simultaneos"]]
