"""Backtest de opções binárias sem olhar o futuro.

Regras:
- sinal decidido no FECHAMENTO da vela t;
- entrada no OPEN da vela t+1; vencimento no CLOSE da vela t+E;
- CALL ganha se saída > entrada; PUT ganha se saída < entrada; igual = empate (devolve);
- sem sobreposição: só uma operação aberta por vez (por ativo);
- operações que atravessam buracos nos dados (fim de semana) são descartadas;
- expiração >= 15 min: só entra em vela que abre em múltiplo de 15 min.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import strategy as st


def simular(df: pd.DataFrame, sinais: np.ndarray, exp: int,
            permitir_sobreposicao: bool = False) -> pd.DataFrame:
    n = len(df)
    sinais = np.asarray(sinais)
    if n < exp + 2:
        return _vazio()
    o = df["open"].to_numpy()
    c = df["close"].to_numpy()
    t_ns = pd.DatetimeIndex(df.index).as_unit("ns").asi8  # nanossegundos
    passo = 60 * 10**9
    cand = np.flatnonzero(sinais[: n - exp - 1] != 0) if n - exp - 1 > 0 else np.array([], int)
    if len(cand) == 0:
        return _vazio()
    # contiguidade: vela t+E deve estar exatamente E minutos depois de t
    ok = (t_ns[cand + exp] - t_ns[cand]) == exp * passo
    if exp >= 15:
        prox_min = ((t_ns[cand] // passo) + 1) % 15
        ok &= prox_min == 0
    cand = cand[ok]
    if len(cand) == 0:
        return _vazio()
    if not permitir_sobreposicao:
        sel = []
        livre = -1
        for t in cand.tolist():
            if t >= livre:
                sel.append(t)
                livre = t + exp
        cand = np.asarray(sel, dtype=int)
    d = sinais[cand].astype(int)
    ent = o[cand + 1]
    sai = c[cand + exp]
    mov = np.sign(sai - ent)
    empate = mov == 0
    win = (mov == d) & ~empate
    tempo = df.index[cand + 1]
    return pd.DataFrame({
        "t": cand, "entrada_hora": tempo, "dir": d, "entrada": ent, "saida": sai,
        "win": win.astype(int), "empate": empate.astype(int),
        "dia": tempo.normalize(),
    })


def _vazio() -> pd.DataFrame:
    return pd.DataFrame(columns=["t", "entrada_hora", "dir", "entrada", "saida",
                                 "win", "empate", "dia"])


def rodar(df: pd.DataFrame, familia: str, params: dict, usd_index=None,
          exposicao: int = 0, feats: pd.DataFrame | None = None) -> pd.DataFrame:
    if feats is None:
        feats = st.calcular_features(df, familia, usd_index, exposicao)
    s = st.gerar_sinais(feats, familia, params)
    return simular(df, s, int(params.get("exp", 1)))


def sem_empates(trades: pd.DataFrame) -> pd.DataFrame:
    return trades[trades["empate"] == 0]
