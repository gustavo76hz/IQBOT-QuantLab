import numpy as np
import pandas as pd
import pytest

import backtest as bt
import sintetico
import strategy as st


def _df(cores, inicio="2026-10-06 10:00"):
    idx = pd.date_range(inicio, periods=len(cores), freq="1min", tz="UTC")
    o = np.ones(len(cores)); c = o + np.array(cores) * 0.001
    return pd.DataFrame({"open": o, "high": np.maximum(o, c), "low": np.minimum(o, c), "close": c}, index=idx)


def test_mhi1_minoria_definicao():
    # quadrante 10:00-10:04: cores +,+,+,-,-  -> 3 últimas (+,-,-) maioria vermelha -> minoria = CALL na 10:05
    df = _df([1, 1, 1, -1, -1, 1, 1])
    f = st.features_base(df)
    s = st.gerar_sinais(f, "quadrante", {"tatica": "mhi1_minoria", "exp": 1})
    assert s[4] == 1 and (s[:4] == 0).all()   # decisão no fechamento da vela 10:04


def test_torres_e_mosqueteiros():
    df = _df([-1, 1, 1, 0, 0])
    f = st.features_base(df)
    assert st.gerar_sinais(f, "quadrante", {"tatica": "torres_gemeas"})[2] == -1      # vela4 = cor vela1
    assert st.gerar_sinais(f, "quadrante", {"tatica": "tres_mosqueteiros"})[2] == 1   # vela4 = cor vela3


@pytest.mark.parametrize("tatica", list(st.TATICAS))
def test_ao_vivo_igual_backtest(tatica):
    df = sintetico.gerar(2, seed=4)
    p = {"tatica": tatica, "exp": 1}
    s = st.gerar_sinais(st.calcular_features(df, "quadrante"), "quadrante", p)
    for t in range(600, 700):
        assert st.decidir_agora(df.iloc[: t + 1], "quadrante", p) == s[t]
    assert len(bt.simular(df, s, 1)) > 50
