import numpy as np
import pandas as pd

import catalogo
import strategy as st
from sintetico import gerar


def test_confluencia_so_entra_com_n_sinais_concordando():
    df = gerar(dias=4, seed=7)
    f = st.features_base(df)
    s = st.gerar_sinais(f, "confluencia", {"n": 5})
    idx = np.nonzero(s)[0]
    assert len(idx) > 0
    votos = f[["v_vela", "v_seq3", "v_rsi", "v_boll", "v_grande", "v_pavio", "v_z5"]].to_numpy()
    for i in idx:
        v = votos[i][votos[i] != 0]
        assert len(v) >= 5 and (v == s[i]).all()            # todos os votos na mesma direção do sinal


def test_decisao_ao_vivo_igual_ao_backtest():
    df = gerar(dias=3, seed=3)
    p = catalogo.obter("confluencia_4_m5").params_completos()
    s = st.gerar_sinais(st.features_base(df), "confluencia", p)
    for fim in (1500, 2200, 2999):
        assert st.decidir_agora(df.iloc[:fim + 1], "confluencia", p) == s[fim]


def test_catalogo_confluencia_so_pares_de_mercado():
    for ch in ("confluencia_5_m5", "confluencia_4_m5"):
        e = catalogo.obter(ch)
        assert e.mercados == ("op",) and e.exp == 5 and e.validavel
