"""Monitor de invalidação: acompanha o acerto AO VIVO da estratégia em uso."""
from __future__ import annotations

from dataclasses import dataclass

import stats


@dataclass
class Situacao:
    n: int
    vitorias: int
    acerto: float
    ic: tuple[float, float]
    breakeven: float
    invalidada: bool
    comprovada: bool
    faltam_para_provar: int | None
    texto: str


def avaliar(df_ops, payout_medio: float | None = None, min_operacoes: int = 40) -> Situacao:
    fech = df_ops[df_ops["resultado"].isin(["win", "loose"])] if len(df_ops) else df_ops
    n = int(len(fech))
    v = int((fech["resultado"] == "win").sum()) if n else 0
    pay = payout_medio if payout_medio else (float(fech["payout"].mean()) if n else 0.85)
    be = stats.breakeven(pay)
    lo, hi = stats.wilson(v, n)
    p = v / n if n else 0.0
    invalidada = n >= min_operacoes and hi < be
    comprovada = n >= min_operacoes and lo > be
    faltam = None
    if n and p > be and not comprovada:
        # nunca menos que o mínimo do monitor: poucas operações enganam
        faltam = max(0, max(min_operacoes, stats.operacoes_para_ic_acima(p, be)) - n)
    if n == 0:
        txt = "sem operações fechadas ainda"
    elif invalidada:
        txt = f"INVALIDADA: IC95% [{lo:.1%}, {hi:.1%}] inteiro abaixo do breakeven {be:.1%}"
    elif comprovada:
        txt = f"vantagem estatística ao vivo: IC95% [{lo:.1%}, {hi:.1%}] acima de {be:.1%}"
    elif p <= be:
        txt = f"acerto {p:.1%} abaixo do breakeven {be:.1%} (n={n}) — ainda inconclusivo"
    else:
        txt = (f"acerto {p:.1%} (n={n}, IC95% {lo:.0%}-{hi:.0%}) — amostra pequena; "
               f"faltam ≥{faltam} operações para tirar conclusão")
    return Situacao(n, v, p, (lo, hi), be, invalidada, comprovada, faltam, txt)
