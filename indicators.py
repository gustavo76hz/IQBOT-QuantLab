"""Indicadores técnicos em pandas/numpy. Todos são CAUSAIS: o valor na linha t
usa apenas dados até a linha t (testado em tests/test_lookahead.py)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    ganho = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    perda = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = ganho / perda.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(perda != 0, 100.0).where(~ganho.isna())


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                      (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    m = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return m - k * sd, m, m + k * sd, (close - m) / sd.replace(0, np.nan)


def stochastic(df: pd.DataFrame, n: int = 14, d: int = 3):
    lo = df["low"].rolling(n, min_periods=n).min()
    hi = df["high"].rolling(n, min_periods=n).max()
    k = 100 * (df["close"] - lo) / (hi - lo).replace(0, np.nan)
    return k, k.rolling(d, min_periods=d).mean()


def macd(close: pd.Series, f: int = 12, s: int = 26, sig: int = 9):
    linha = ema(close, f) - ema(close, s)
    sinal = ema(linha, sig)
    return linha, sinal, linha - sinal


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff()
    dn = -df["low"].diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    ndm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    a = atr(df, n)
    pdi = 100 * pdm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / a
    ndi = 100 * ndm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / a
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def percentil_movel(s: pd.Series, janela: int) -> pd.Series:
    """Posição (0..1) do valor atual dentro da janela passada (inclui o atual).
    Implementado por rank para ser causal."""
    return s.rolling(janela, min_periods=max(20, janela // 4)).rank(pct=True)
