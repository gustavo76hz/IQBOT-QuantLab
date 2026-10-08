"""Estatística usada em todo o projeto (só numpy/pandas, sem scikit-learn).

Conceitos:
  breakeven  = 1 / (1 + payout)            -> acerto mínimo para não perder dinheiro
  expectativa por entrada = p*(1+payout) - 1 (em fração da entrada)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

Z95 = 1.959963984540054


def breakeven(payout: float) -> float:
    return 1.0 / (1.0 + payout)


def expectativa(acerto: float, payout: float) -> float:
    """Resultado médio por entrada, em fração da entrada (ex.: -0.065 = -6,5%)."""
    return acerto * (1.0 + payout) - 1.0


def wilson(vitorias: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Intervalo de confiança de Wilson para uma proporção."""
    if n <= 0:
        return 0.0, 1.0
    p = vitorias / n
    den = 1 + z * z / n
    centro = (p + z * z / (2 * n)) / den
    meia = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centro - meia), min(1.0, centro + meia)


def _norm_ppf(q: float) -> float:
    """Inversa da normal padrão (aproximação de Acklam, erro < 1e-9)."""
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]
    plow, phigh = 0.02425, 1 - 0.02425
    if q < plow:
        x = math.sqrt(-2 * math.log(q))
        return (((((c[0] * x + c[1]) * x + c[2]) * x + c[3]) * x + c[4]) * x + c[5]) / \
               ((((d[0] * x + d[1]) * x + d[2]) * x + d[3]) * x + 1)
    if q > phigh:
        x = math.sqrt(-2 * math.log(1 - q))
        return -(((((c[0] * x + c[1]) * x + c[2]) * x + c[3]) * x + c[4]) * x + c[5]) / \
                ((((d[0] * x + d[1]) * x + d[2]) * x + d[3]) * x + 1)
    x = q - 0.5
    r = x * x
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * x / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def norm_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2))


def p_valor_unilateral(vitorias: int, n: int, p0: float) -> float:
    """P-valor (aproximação normal com correção de continuidade) de acerto > p0."""
    if n <= 0:
        return 1.0
    sd = math.sqrt(n * p0 * (1 - p0))
    z = (vitorias - 0.5 - n * p0) / sd
    return norm_sf(z)


def operacoes_necessarias(acerto_real: float, breakeven_: float,
                          alpha: float = 0.05, poder: float = 0.80) -> int:
    """Quantas operações são precisas para provar acerto_real > breakeven
    (teste unilateral, nível alpha, poder 'poder')."""
    if acerto_real <= breakeven_:
        return 10**9
    za = _norm_ppf(1 - alpha)
    zb = _norm_ppf(poder)
    p0, p1 = breakeven_, acerto_real
    n = ((za * math.sqrt(p0 * (1 - p0)) + zb * math.sqrt(p1 * (1 - p1))) / (p1 - p0)) ** 2
    return int(math.ceil(n))


def operacoes_para_ic_acima(acerto_real: float, breakeven_: float) -> int:
    """Nº de operações para que o limite inferior do IC95% (Wilson) fique acima
    do breakeven SE o acerto observado for exatamente acerto_real."""
    if acerto_real <= breakeven_:
        return 10**9
    n = 10
    while n < 10**7:
        lo, _ = wilson(int(round(acerto_real * n)), n)
        if lo > breakeven_:
            return n
        n = int(n * 1.05) + 1
    return n


def bootstrap_por_dia(resultados: pd.DataFrame, n_boot: int = 2000,
                      seed: int = 7) -> tuple[float, float]:
    """IC 95% do acerto reamostrando DIAS inteiros (operações do mesmo dia
    não são independentes). resultados: colunas 'dia' e 'win' (0/1)."""
    if len(resultados) == 0:
        return 0.0, 1.0
    g = resultados.groupby("dia")["win"].agg(["sum", "count"])
    s = g["sum"].to_numpy(float)
    c = g["count"].to_numpy(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(g), size=(n_boot, len(g)))
    taxas = s[idx].sum(1) / np.maximum(c[idx].sum(1), 1)
    return float(np.quantile(taxas, 0.025)), float(np.quantile(taxas, 0.975))


def acerto_sem_dias_concentrados(resultados: pd.DataFrame, frac: float = 0.05) -> float:
    """Acerto depois de retirar os 'frac' dias com MAIS vitórias líquidas
    (vitórias - derrotas). Se a vantagem some, ela dependia de poucos dias."""
    if len(resultados) == 0:
        return float("nan")
    g = resultados.groupby("dia")["win"].agg(["sum", "count"])
    g["liq"] = 2 * g["sum"] - g["count"]
    k = max(1, int(len(g) * frac))
    resto = g.sort_values("liq", ascending=False).iloc[k:]
    return float(resto["sum"].sum() / max(resto["count"].sum(), 1))


def falsas_descobertas_esperadas(n_testes: int, alpha: float = 0.05) -> float:
    return n_testes * alpha


def benjamini_hochberg(pvalores: list[float], q: float = 0.10) -> list[bool]:
    """Controle da taxa de falsas descobertas. Retorna quais passam."""
    m = len(pvalores)
    if m == 0:
        return []
    ordem = np.argsort(pvalores)
    passa = np.zeros(m, bool)
    limite = -1
    for rank, i in enumerate(ordem, start=1):
        if pvalores[i] <= q * rank / m:
            limite = rank
    if limite > 0:
        passa[ordem[:limite]] = True
    return passa.tolist()


@dataclass
class Resumo:
    n: int
    vitorias: int
    empates: int
    acerto: float
    ic_baixo: float
    ic_alto: float
    breakeven: float
    expectativa: float

    def acima_do_breakeven(self) -> bool:
        return self.ic_baixo > self.breakeven

    def texto(self) -> str:
        return (f"n={self.n} acerto={self.acerto:.1%} IC95%=[{self.ic_baixo:.1%}, "
                f"{self.ic_alto:.1%}] breakeven={self.breakeven:.1%} "
                f"expectativa={self.expectativa:+.1%}/entrada")


def resumir(wins: np.ndarray | pd.Series, payout: float, empates: int = 0) -> Resumo:
    w = np.asarray(wins, dtype=float)
    n = int(len(w))
    v = int(w.sum())
    p = v / n if n else 0.0
    lo, hi = wilson(v, n)
    return Resumo(n, v, empates, p, lo, hi, breakeven(payout), expectativa(p, payout))
