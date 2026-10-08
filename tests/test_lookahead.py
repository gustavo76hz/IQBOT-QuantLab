"""Nenhuma feature/sinal pode mudar quando acrescentamos dados FUTUROS."""
import numpy as np
import pytest

import sintetico
import strategy as st


@pytest.fixture(scope="module")
def df():
    return sintetico.gerar(6, seed=9)


@pytest.mark.parametrize("familia", [f for f in st.FAMILIAS if f != "dxy"])
def test_features_sem_futuro(df, familia):
    cheio = st.calcular_features(df, familia)
    for corte in (2000, 3500, 5000):
        parcial = st.calcular_features(df.iloc[:corte], familia)
        a = cheio.iloc[corte - 1].to_numpy(float)
        b = parcial.iloc[-1].to_numpy(float)
        assert np.allclose(a, b, equal_nan=True, rtol=1e-9, atol=1e-12), familia


def test_dxy_sem_futuro():
    dados = sintetico.conjunto(4, 5)
    closes = {k: v["close"] for k, v in dados.items()}
    alvo = dados["EURUSD"]
    outros = {k: v for k, v in closes.items() if k != "EURUSD"}
    u = st.indice_usd(outros)
    cheio = st.calcular_features(alvo, "dxy", u, -1)
    corte = 4000
    parcial = st.calcular_features(alvo.iloc[:corte], "dxy", u.iloc[:corte], -1)
    assert np.allclose(cheio.iloc[corte - 1].to_numpy(float), parcial.iloc[-1].to_numpy(float), equal_nan=True)


@pytest.mark.parametrize("familia,params", [
    ("regime", {"k": 5, "z": 1.5, "regime": "todas", "lado": 1, "exp": 1}),
    ("sequencia", {"n": 3, "lado": -1, "exp": 1}),
    ("bollinger", {"k": 2.0, "lado": -1, "exp": 5}),
])
def test_decisao_ao_vivo_igual_backtest(df, familia, params):
    f = st.calcular_features(df, familia)
    s = st.gerar_sinais(f, familia, params)
    for t in range(1500, 1700):
        assert st.decidir_agora(df.iloc[: t + 1], familia, params) == s[t]
