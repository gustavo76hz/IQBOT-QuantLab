import numpy as np
import pandas as pd

import backtest as bt


def _df(closes, inicio="2024-01-01 10:00"):
    idx = pd.date_range(inicio, periods=len(closes), freq="1min", tz="UTC")
    c = np.array(closes, float)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c), "low": np.minimum(o, c), "close": c}, index=idx)


def test_entrada_no_open_seguinte_e_saida_no_close():
    df = _df([1.0, 1.0, 1.1, 1.2, 1.1, 1.0])
    s = np.array([1, 0, 0, 0, 0, 0])
    tr = bt.simular(df, s, 2)
    assert len(tr) == 1
    assert tr.iloc[0]["entrada"] == df["open"].iloc[1]
    assert tr.iloc[0]["saida"] == df["close"].iloc[2]
    assert tr.iloc[0]["win"] == 1


def test_sem_sobreposicao():
    df = _df(list(np.linspace(1, 2, 30)))
    s = np.ones(30, int)
    tr = bt.simular(df, s, 5)
    assert (np.diff(tr["t"].to_numpy()) >= 5).all()


def test_empate_e_put():
    df = _df([1.0, 1.0, 1.0, 0.9, 0.9])
    tr = bt.simular(df, np.array([-1, 0, -1, 0, 0]), 1)
    assert tr.iloc[0]["empate"] == 1 and tr.iloc[0]["win"] == 0
    assert tr.iloc[1]["win"] == 1  # PUT: 1.0 -> 0.9


def test_buraco_nos_dados_descarta():
    df = _df([1.0, 1.1, 1.2, 1.3])
    df.index = df.index[:2].append(df.index[2:] + pd.Timedelta(hours=48))
    tr = bt.simular(df, np.array([1, 1, 0, 0]), 1)
    assert list(tr["t"]) == [0]


def test_expiracao_15_so_no_quarto_de_hora():
    df = _df(list(np.linspace(1, 2, 80)), inicio="2024-01-01 10:00")
    tr = bt.simular(df, np.ones(80, int), 15)
    assert all(h.minute % 15 == 0 for h in tr["entrada_hora"])
