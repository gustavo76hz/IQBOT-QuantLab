"""Gerador de candles M1 sintéticos para os CONTROLES do pipeline.

- aleatorio: passeio aleatório com volatilidade que muda (agrupamento) e
  sazonalidade intradiária -> o pipeline NÃO pode aprovar nada.
- plantado: igual, mas com uma deriva para cima numa hora fixa -> o pipeline
  TEM que achar (família 'hora', lado CALL, exp >= 5).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def gerar(dias: int = 500, seed: int = 1, preco0: float = 1.1, decimais: int = 5,
          hora_plantada: int | None = None, forca: float = 0.12,
          inicio: str = "2021-01-04") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(inicio, periods=dias * 1440, freq="1min", tz="UTC")
    idx = idx[idx.dayofweek < 5]  # sem fim de semana
    n = len(idx)
    h = idx.hour.to_numpy()
    saz = 0.6 + 0.8 * np.exp(-((h - 13.5) ** 2) / 18)  # mais vol na sobreposição Londres/NY
    # log-vol com reversão à média (agrupamento de volatilidade)
    lv = np.zeros(n)
    e = rng.standard_normal(n) * 0.02
    for i in range(1, n):
        lv[i] = 0.999 * lv[i - 1] + e[i]
    sig = 0.00012 * saz * np.exp(lv)
    r = sig * rng.standard_normal(n)
    if hora_plantada is not None:
        r = r + np.where(h == hora_plantada, forca * sig, 0.0)
    c = preco0 * np.exp(np.cumsum(r))
    o = np.empty(n)
    o[0] = preco0
    o[1:] = c[:-1]
    ext = np.abs(rng.standard_normal((2, n))) * sig * 0.4
    hi = np.maximum(o, c) * np.exp(ext[0])
    lo = np.minimum(o, c) * np.exp(-ext[1])
    df = pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c,
                       "volume": rng.integers(1, 100, n).astype(float)}, index=idx)
    return df.round(decimais)


def conjunto(n_ativos: int = 3, dias: int = 500, plantado: bool = False, seed: int = 0):
    nomes = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF"][:n_ativos]
    out = {}
    for i, nome in enumerate(nomes):
        dec = 3 if "JPY" in nome else 5
        p0 = 140.0 if "JPY" in nome else 1.1
        out[nome] = gerar(dias, seed + i, p0, dec, hora_plantada=14 if plantado else None)
    return out
