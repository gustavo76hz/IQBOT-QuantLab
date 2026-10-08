import numpy as np
import pandas as pd

import stats


def test_breakeven_e_expectativa():
    assert abs(stats.breakeven(0.87) - 0.5348) < 1e-3
    assert abs(stats.expectativa(0.5, 0.87) - (-0.065)) < 1e-9


def test_wilson_valor_conhecido():
    lo, hi = stats.wilson(50, 100)
    assert abs(lo - 0.4038) < 1e-3 and abs(hi - 0.5962) < 1e-3


def test_operacoes_para_provar_58_contra_532():
    n_ic = stats.operacoes_para_ic_acima(0.58, 0.532)
    assert 380 <= n_ic <= 440  # "~400 operações" (spec)
    n_poder = stats.operacoes_necessarias(0.58, 0.532)  # com 80% de poder: mais
    assert n_poder > n_ic


def test_bh():
    assert stats.benjamini_hochberg([0.001, 0.5, 0.9], 0.1) == [True, False, False]


def test_bootstrap_por_dia_mais_largo_quando_dias_correlacionados():
    rng = np.random.default_rng(0)
    dias = np.repeat(np.arange(100), 20)
    efeito = rng.choice([0, 1], 100)[dias]  # dia inteiro ganha ou perde
    df = pd.DataFrame({"dia": dias, "win": efeito})
    lo, hi = stats.bootstrap_por_dia(df)
    lo_w, hi_w = stats.wilson(int(df.win.sum()), len(df))
    assert (hi - lo) > 2 * (hi_w - lo_w)


def test_sem_dias_concentrados():
    df = pd.DataFrame({"dia": [1] * 50 + list(range(2, 52)), "win": [1] * 50 + [0, 1] * 25})
    assert stats.acerto_sem_dias_concentrados(df, 0.02) < 0.6
