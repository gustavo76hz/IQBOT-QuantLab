"""Laboratório de estratégias: o pipeline de validação (seção 6 do projeto).

Para cada família de hipóteses:
 1. Divide o tempo em: TREINO (60%) | TESTE (40%) | PERÍODO NOVO (últimos meses, intocado).
 2. No TREINO: roda todas as combinações (parâmetro x expiração x ativo) e escolhe o
    LADO (seguir/inverter). Conta quantos testes foram feitos.
 3. Só quem passa no treino (IC95% inferior > breakeven, n mínimo) vai ao TESTE, com
    tudo congelado. No teste exige: IC95% inferior > breakeven, Benjamini-Hochberg,
    bootstrap por DIA, resultado sem os dias mais concentrados, maioria dos blocos
    cronológicos e dos ativos acima do breakeven.
 4. Sobreviventes vão ao PERÍODO NOVO com a regra congelada. Só aí são "APROVADAS".
 5. Walk-forward (janelas móveis) das aprovadas, para mostrar estabilidade.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

import backtest as bt
import stats
import strategy as st


@dataclass
class Criterios:
    payout: dict = field(default_factory=lambda: {1: 0.85, 2: 0.85, 3: 0.85, 5: 0.85, 15: 0.80})
    exps: tuple = (1, 5, 15)
    frac_treino: float = 0.60
    meses_novos: int = 6
    dias_novos: int | None = None  # se definido, substitui meses_novos (dados curtos da IQ)
    n_min_treino: int = 300
    n_min_teste: int = 200
    q_bh: float = 0.10
    blocos: int = 4
    frac_dias_concentrados: float = 0.05

    def be(self, exp: int) -> float:
        return stats.breakeven(self.payout.get(exp, 0.85))


@dataclass
class Resultado:
    familia: str
    params: dict
    exp: int
    ativo: str
    lado: int
    treino: dict
    teste: dict | None = None
    novo: dict | None = None
    robustez: dict | None = None
    status: str = "reprovada no treino"


def _stats_trades(tr: pd.DataFrame, lado: int, payout: float) -> dict:
    t = tr[tr["empate"] == 0]
    if len(t) == 0:
        return {"n": 0, "acerto": float("nan"), "ic_baixo": 0.0, "ic_alto": 1.0, "empates": int(tr["empate"].sum())}
    w = t["win"].to_numpy() if lado == 1 else 1 - t["win"].to_numpy()
    r = stats.resumir(w, payout, int(tr["empate"].sum()))
    return {"n": r.n, "vitorias": r.vitorias, "acerto": r.acerto, "ic_baixo": r.ic_baixo,
            "ic_alto": r.ic_alto, "empates": r.empates, "expectativa": r.expectativa}


def _wins(tr: pd.DataFrame, lado: int) -> pd.DataFrame:
    t = tr[tr["empate"] == 0].copy()
    t["dia"] = t["entrada_hora"].dt.normalize()
    if lado == -1:
        t["win"] = 1 - t["win"]
    return t


def definir_periodos(indice_ini: pd.Timestamp, indice_fim: pd.Timestamp, c: Criterios):
    novo_ini = indice_fim - (pd.Timedelta(days=c.dias_novos) if c.dias_novos
                             else pd.DateOffset(months=c.meses_novos))
    corte = indice_ini + (novo_ini - indice_ini) * c.frac_treino
    return {"inicio": indice_ini, "corte_treino": corte, "novo_inicio": novo_ini, "fim": indice_fim}


def _rotular(tr: pd.DataFrame, per: dict) -> pd.Series:
    h = tr["entrada_hora"]
    return np.where(h < per["corte_treino"], "treino", np.where(h < per["novo_inicio"], "teste", "novo"))


def gerar_trades(dados: dict[str, pd.DataFrame], familia: str, c: Criterios,
                 closes_usd: dict[str, pd.Series] | None = None, log=print):
    """Roda todas as combinações em todos os ativos. Retorna {(params_json, exp, ativo): trades}."""
    fam = st.FAMILIAS[familia]
    saida = {}
    for ativo, df in dados.items():
        usd, expo = None, 0
        if fam.precisa_dxy:
            base = ativo.upper()
            if base not in st.EXPOSICAO_USD or closes_usd is None:
                continue
            outros = {k: v for k, v in closes_usd.items() if k.upper() != base}
            if len(outros) < 2:
                continue
            usd = st.indice_usd(outros).reindex(df.index).ffill()
            expo = st.EXPOSICAO_USD[base]
        feats = st.calcular_features(df, familia, usd, expo)
        for p in fam.combinacoes():
            for e in c.exps:
                pp = dict(p, lado=1, exp=e)
                sig = st.gerar_sinais(feats, familia, pp)
                tr = bt.simular(df, sig, e)
                if len(tr):
                    tr = pd.DataFrame({"entrada_hora": tr["entrada_hora"].to_numpy(),
                                       "win": tr["win"].to_numpy(np.int8),
                                       "empate": tr["empate"].to_numpy(np.int8)})
                    tr["ativo"] = ativo
                saida[(json.dumps(p, sort_keys=True), e, ativo)] = tr
        log(f"  {familia}: {ativo} pronto")
    return saida


def avaliar(trades: dict, familia: str, per: dict, c: Criterios) -> tuple[list[Resultado], dict]:
    # agrupa: por ativo + todos os ativos juntos
    grupos: dict[tuple, list[pd.DataFrame]] = {}
    for (pj, e, ativo), tr in trades.items():
        if len(tr) == 0:
            continue
        tr = tr.copy()
        tr["periodo"] = _rotular(tr, per)
        grupos.setdefault((pj, e, ativo), []).append(tr)
        grupos.setdefault((pj, e, "TODOS"), []).append(tr)
    resultados: list[Resultado] = []
    n_testes = 0
    sig50 = 0
    for (pj, e, ativo), lst in grupos.items():
        tr = pd.concat(lst, ignore_index=True)
        trein = tr[tr["periodo"] == "treino"]
        sem = trein[trein["empate"] == 0]
        n_testes += 2  # os dois lados contam como testes
        if len(sem) < c.n_min_treino:
            continue
        lado = 1 if sem["win"].mean() >= 0.5 else -1
        v = int(sem["win"].sum()) if lado == 1 else int(len(sem) - sem["win"].sum())
        if stats.p_valor_unilateral(v, len(sem), 0.5) < 0.025:  # bilateral 5%
            sig50 += 1
        s_tr = _stats_trades(trein, lado, c.payout.get(e, 0.85))
        r = Resultado(familia, json.loads(pj), e, ativo, lado, s_tr)
        r._tr = tr  # type: ignore[attr-defined]
        if s_tr["ic_baixo"] > c.be(e):
            r.status = "passou no treino"
        resultados.append(r)
    candidatos = [r for r in resultados if r.status == "passou no treino"]
    # TESTE
    pvals = []
    for r in candidatos:
        tr = r._tr  # type: ignore[attr-defined]
        teste = tr[tr["periodo"] == "teste"]
        r.teste = _stats_trades(teste, r.lado, c.payout.get(r.exp, 0.85))
        v, n = r.teste.get("vitorias", 0), r.teste["n"]
        pvals.append(stats.p_valor_unilateral(v, n, c.be(r.exp)) if n else 1.0)
    passa_bh = stats.benjamini_hochberg(pvals, c.q_bh)
    for r, okbh in zip(candidatos, passa_bh):
        tr = r._tr  # type: ignore[attr-defined]
        teste = _wins(tr[tr["periodo"] == "teste"], r.lado)
        be = c.be(r.exp)
        rob = {"bh": bool(okbh)}
        if len(teste) >= c.n_min_teste:
            rob["boot_dia"] = stats.bootstrap_por_dia(teste)
            rob["sem_dias_top"] = stats.acerto_sem_dias_concentrados(teste, c.frac_dias_concentrados)
            ts = teste.sort_values("entrada_hora")
            cortes = np.linspace(0, len(ts), c.blocos + 1).astype(int)
            rob["blocos"] = [float(ts["win"].iloc[a:b].mean()) for a, b in zip(cortes[:-1], cortes[1:]) if b > a]
            if r.ativo == "TODOS":
                rob["por_ativo"] = teste.groupby("ativo")["win"].mean().round(4).to_dict()
        ok = (len(teste) >= c.n_min_teste and r.teste["ic_baixo"] > be and okbh
              and rob["boot_dia"][0] > be and rob["sem_dias_top"] > be
              and np.mean(np.array(rob["blocos"]) > be) >= 0.75
              and (r.ativo != "TODOS" or np.mean(np.array(list(rob["por_ativo"].values())) > be) > 0.5))
        r.robustez = rob
        r.status = "passou no teste" if ok else "reprovada no teste"
    # PERÍODO NOVO (regra congelada)
    for r in candidatos:
        if r.status != "passou no teste":
            continue
        tr = r._tr  # type: ignore[attr-defined]
        r.novo = _stats_trades(tr[tr["periodo"] == "novo"], r.lado, c.payout.get(r.exp, 0.85))
        r.status = "APROVADA" if (r.novo["n"] >= 100 and r.novo["ic_baixo"] > c.be(r.exp)) else "reprovada no período novo"
    resumo = {"familia": familia, "testes_no_treino": n_testes,
              "passaram_treino": len(candidatos),
              "diferentes_de_50_no_treino": sig50,
              "falsas_esperadas_treino_5pct": stats.falsas_descobertas_esperadas(n_testes // 2, 0.05),
              "passaram_teste": sum(r.status in ("passou no teste", "APROVADA", "reprovada no período novo") for r in candidatos),
              "aprovadas": sum(r.status == "APROVADA" for r in candidatos)}
    return resultados, resumo


def walk_forward(dados: dict[str, pd.DataFrame], familia: str, params: dict, exp: int,
                 ativo: str, meses_treino: int = 12, meses_teste: int = 3,
                 closes_usd=None) -> dict:
    """Escolhe o lado em cada janela de treino e mede na janela seguinte."""
    c = Criterios(exps=(exp,))
    tr_all = []
    for (pj, e, at), tr in gerar_trades({k: v for k, v in dados.items() if ativo in ("TODOS", k)},
                                         familia, c, closes_usd, log=lambda *_: None).items():
        if pj == json.dumps(params, sort_keys=True) and len(tr):
            tr_all.append(tr)
    if not tr_all:
        return {"janelas": 0}
    tr = pd.concat(tr_all).sort_values("entrada_hora")
    tr = tr[tr["empate"] == 0]
    ini, fim = tr["entrada_hora"].min(), tr["entrada_hora"].max()
    oos = []
    t0 = ini
    while True:
        t1 = t0 + pd.DateOffset(months=meses_treino)
        t2 = t1 + pd.DateOffset(months=meses_teste)
        if t1 >= fim:
            break
        a = tr[(tr["entrada_hora"] >= t0) & (tr["entrada_hora"] < t1)]
        b = tr[(tr["entrada_hora"] >= t1) & (tr["entrada_hora"] < t2)]
        if len(a) and len(b):
            lado = 1 if a["win"].mean() >= 0.5 else -1
            w = b["win"].to_numpy() if lado == 1 else 1 - b["win"].to_numpy()
            oos.append((str(t1.date()), lado, len(w), float(w.mean())))
        t0 = t0 + pd.DateOffset(months=meses_teste)
    todos = np.concatenate([[1] * int(round(n * p)) + [0] * (n - int(round(n * p))) for _, _, n, p in oos]) if oos else np.array([])
    lo, hi = stats.wilson(int(todos.sum()), len(todos))
    return {"janelas": len(oos), "detalhe": oos, "n": int(len(todos)),
            "acerto": float(todos.mean()) if len(todos) else float("nan"), "ic": (lo, hi)}


def resultados_para_json(resultados: list[Resultado]) -> list[dict]:
    out = []
    for r in resultados:
        d = asdict(r)
        out.append(d)
    return out


def congelar(r: Resultado, caminho: Path, periodos: dict):
    """Grava a regra aprovada para o bot usar (data/estrategia_congelada.json)."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps({
        "familia": r.familia, "params": dict(r.params, lado=r.lado, exp=r.exp),
        "ativo": r.ativo, "treino": r.treino, "teste": r.teste, "novo": r.novo,
        "periodos": {k: str(v) for k, v in periodos.items()},
        "aviso": "Regra congelada. Não altere parâmetros: qualquer ajuste invalida a validação.",
    }, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
