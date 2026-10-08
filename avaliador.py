"""Avaliador: testa cada estratégia do catálogo em cada par (backtest com velas da IQ)
e decide, com regras FIXAS definidas antes de ver os resultados, o que está APROVADO.

Duas partes:
  1) atualizar_velas()  baixa só as velas que faltam (usa a conexão do bot, sem 2º login);
  2) calcular()         roda os backtests (pesado) — o app chama em PROCESSO SEPARADO:
                        python avaliador.py --calcular   (lê data/iq, grava data/avaliacao.json)

Regras de aprovação (config automacao.aprovacao):
  - n >= n_min operações na janela (padrão 300);
  - significativo contra 50% depois da correção de Benjamini-Hochberg (são muitas combinações);
  - acerto >= metade_min em CADA metade do período (estabilidade, padrão 51,5%);
  - últimos 7 dias >= ultimos7_min (padrão 50%) quando houver ao menos n7_min operações;
  - acerto >= breakeven(payout atual) - margem_breakeven (padrão 2 pontos).
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True   # roda em outro processo: também não cria __pycache__

import argparse
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import backtest
import catalogo
import stats
import strategy as st
from mercado import tipo_par
from risk import FUSO_BR

RAIZ = Path(__file__).parent
log = logging.getLogger("bot.avaliador")

REGRAS_PADRAO = {"janela_dias": 30, "n_min": 300, "bh_q": 0.05, "metade_min": 0.515,
                 "ultimos7_min": 0.50, "n7_min": 50, "margem_breakeven": 0.02}
PAYOUT_PADRAO = {"op": 0.85, "otc": 0.82}


# --------------------------------------------------------------------------- progresso
def gravar_progresso(arq: Path | None, **kw):
    if arq is None:
        return
    try:
        atual = json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else {}
    except Exception:  # noqa: BLE001
        atual = {}
    atual.update(kw)
    atual["atualizado"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = arq.with_suffix(".tmp")
    tmp.write_text(json.dumps(atual, ensure_ascii=False), encoding="utf-8")
    tmp.replace(arq)


# --------------------------------------------------------------------------- velas
def ler_velas(arq: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(arq, index_col="time", parse_dates=["time"])
        df.index = pd.to_datetime(df.index, utc=True)
        return df[~df.index.duplicated(keep="last")].sort_index()
    except Exception:  # noqa: BLE001
        return None


def atualizar_velas(corretora, ativos: list[str], pasta: Path, progresso: Path | None = None,
                    dias: int = 35, parar=None) -> dict[str, str]:
    """Baixa só o que falta de cada par (ou ~dias de velas se o arquivo não existe)."""
    pasta.mkdir(parents=True, exist_ok=True)
    out = {}
    for i, a in enumerate(ativos):
        if parar is not None and parar.is_set():
            break
        gravar_progresso(progresso, etapa="baixando velas", feito=i, total=len(ativos), msg=a)
        arq = pasta / f"{a}.csv.gz"
        antigo = ler_velas(arq) if arq.exists() else None
        agora = time.time()
        if antigo is not None and len(antigo):
            faltam = int((agora - antigo.index[-1].timestamp()) // 60) + 5
        else:
            faltam = dias * 1440
        faltam = max(10, min(faltam, dias * 1440))
        try:
            novo = corretora.velas(a, 60, faltam)
            df = novo if antigo is None else pd.concat([antigo, novo])
            df = df[~df.index.duplicated(keep="last")].sort_index()
            df = df[df.index >= df.index[-1] - pd.Timedelta(days=dias)]
            df.to_csv(arq, index_label="time", compression="gzip")
            out[a] = f"{len(df)} velas"
        except Exception as e:  # noqa: BLE001
            out[a] = f"erro: {e}"
            log.warning("avaliador: velas de %s falharam: %s", a, e)
    return out


# --------------------------------------------------------------------------- backtest
def _metricas(tr: pd.DataFrame) -> dict:
    """tr: operações sem empate, com coluna win e entrada_hora (UTC)."""
    n = len(tr)
    w = int(tr["win"].sum()) if n else 0
    lo, hi = stats.wilson(w, n) if n else (0.0, 1.0)
    out = {"n": n, "wins": w, "acerto": (w / n) if n else None, "ic": [lo, hi],
           "p": stats.p_valor_unilateral(w, n, 0.5) if n else 1.0}
    if n:
        t = pd.DatetimeIndex(tr["entrada_hora"])
        meio = t[0] + (t[-1] - t[0]) / 2
        m1, m2 = tr[t < meio], tr[t >= meio]
        u7 = tr[t >= t[-1] - pd.Timedelta(days=7)]
        out.update(m1=float(m1["win"].mean()) if len(m1) else None, m2=float(m2["win"].mean()) if len(m2) else None,
                   u7=float(u7["win"].mean()) if len(u7) else None, n7=int(len(u7)))
    else:
        out.update(m1=None, m2=None, u7=None, n7=0)
    return out


def testar_par(df: pd.DataFrame, ativo: str, janela_dias: int) -> list[dict]:
    mercado = tipo_par(ativo)
    if mercado is None or df is None or len(df) < 500:
        return []
    df = df[df.index >= df.index[-1] - pd.Timedelta(days=janela_dias)]
    feats = st.features_base(df)     # calculado uma vez e reaproveitado por todas as estratégias
    linhas = []
    for e in catalogo.para_mercado(mercado):
        if not e.validavel:
            continue
        tr = backtest.sem_empates(backtest.rodar(df, e.familia, e.params_completos(), feats=feats))
        if len(tr) and e.horas_brt.get(mercado):
            h = pd.DatetimeIndex(tr["entrada_hora"]).tz_convert(FUSO_BR).hour
            ini, fim = e.horas_brt[mercado]
            tr = tr[(h >= ini) & (h < fim)]
        m = _metricas(tr)
        linhas.append({"chave": e.chave, "ativo": ativo, "mercado": mercado,
                       "periodo": [str(df.index[0]), str(df.index[-1])], **m})
    return linhas


def aprovar(linhas: list[dict], payouts: dict[str, float], regras: dict) -> list[dict]:
    """Aplica as regras fixas e marca cada combinação como elegível ou não (com motivos)."""
    r = {**REGRAS_PADRAO, **(regras or {})}
    testaveis = [i for i, x in enumerate(linhas) if x["n"] >= r["n_min"]]
    bh = stats.benjamini_hochberg([linhas[i]["p"] for i in testaveis], r["bh_q"]) if testaveis else []
    passou_bh = {i: ok for i, ok in zip(testaveis, bh)}
    for i, x in enumerate(linhas):
        pay = float(payouts.get(x["ativo"]) or PAYOUT_PADRAO[x["mercado"]])
        be = stats.breakeven(pay)
        motivos = []
        if x["n"] < r["n_min"]:
            motivos.append(f"poucas operações ({x['n']} < {r['n_min']})")
        else:
            if not passou_bh.get(i, False):
                motivos.append("não é significativo após a correção (pode ser sorte)")
            if x["acerto"] < be - r["margem_breakeven"]:
                motivos.append(f"acerto {x['acerto']:.1%} abaixo de {be - r['margem_breakeven']:.1%} "
                               f"(empate {be:.1%} com payout {pay:.0%})")
            if (x["m1"] or 0) < r["metade_min"] or (x["m2"] or 0) < r["metade_min"]:
                motivos.append(f"instável: metades {x['m1'] or 0:.1%} / {x['m2'] or 0:.1%}")
            if x["n7"] >= r["n7_min"] and (x["u7"] or 0) < r["ultimos7_min"]:
                motivos.append(f"piorou nos últimos 7 dias ({x['u7']:.1%})")
        x.update(payout=pay, breakeven=be, bh=bool(passou_bh.get(i, False)),
                 elegivel=not motivos, motivos=motivos)
    return linhas


def resumir(linhas: list[dict]) -> dict:
    """Por mercado e estratégia: pares aprovados e o resultado somado deles."""
    out: dict = {}
    for x in linhas:
        d = out.setdefault(x["mercado"], {}).setdefault(x["chave"], {"pares_aprovados": [], "n": 0, "wins": 0,
                                                                      "pares_testados": 0})
        d["pares_testados"] += 1
        if x["elegivel"]:
            d["pares_aprovados"].append(x["ativo"])
            d["n"] += x["n"]
            d["wins"] += x["wins"]
    for m in out.values():
        for d in m.values():
            n, w = d["n"], d["wins"]
            d["acerto"] = (w / n) if n else None
            d["ic"] = list(stats.wilson(w, n)) if n else None
    return out


def calcular(pasta_velas: Path, payouts: dict[str, float], regras: dict | None = None,
             progresso: Path | None = None, ativos: list[str] | None = None) -> dict:
    r = {**REGRAS_PADRAO, **(regras or {})}
    arqs = sorted(pasta_velas.glob("*.csv.gz"))
    if ativos is not None:
        arqs = [a for a in arqs if a.name[:-7] in set(ativos)]
    arqs = [a for a in arqs if tipo_par(a.name[:-7])]
    linhas = []
    for i, arq in enumerate(arqs):
        gravar_progresso(progresso, etapa="testando estratégias", feito=i, total=len(arqs), msg=arq.name[:-7])
        linhas += testar_par(ler_velas(arq), arq.name[:-7], r["janela_dias"])
    linhas = aprovar(linhas, payouts, r)
    res = {"gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"), "regras": r,
           "combinacoes": linhas, "resumo": resumir(linhas),
           "nao_validaveis": [e.chave for e in catalogo.CATALOGO if not e.validavel]}
    return res


def salvar(res: dict, arq: Path):
    tmp = arq.with_suffix(".tmp")
    tmp.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(arq)


def carregar(arq: Path) -> dict | None:
    try:
        return json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else None
    except Exception:  # noqa: BLE001
        return None


def idade_horas(res: dict | None) -> float | None:
    if not res:
        return None
    t = datetime.fromisoformat(res["gerado_em"])
    return (datetime.now(timezone.utc) - t).total_seconds() / 3600


# --------------------------------------------------------------------------- ao vivo
def desempenho_ao_vivo(ops: pd.DataFrame) -> list[dict]:
    """Resultado REAL por estratégia e mercado (sempre com n e IC95%, nunca a média solta)."""
    if ops is None or not len(ops):
        return []
    d = ops[ops["resultado"].isin(["win", "loose"])].copy()
    if not len(d):
        return []
    d["mercado"] = d["ativo"].map(lambda a: tipo_par(str(a)) or "outro")
    por_id = {e.id: e for e in catalogo.CATALOGO}
    out = []
    for (eid, m), g in d.groupby(["estrategia", "mercado"]):
        n, w = len(g), int((g["resultado"] == "win").sum())
        lo, hi = stats.wilson(w, n)
        e = por_id.get(eid)
        out.append({"chave": e.chave if e else None, "nome": e.nome if e else str(eid).split(":")[0],
                    "mercado": m, "n": n, "acerto": w / n, "ic": [lo, hi], "lucro": float(g["lucro"].sum())})
    return sorted(out, key=lambda x: -x["n"])


# --------------------------------------------------------------------------- CLI (processo separado)
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--calcular", action="store_true")
    ap.add_argument("--pasta", default=str(RAIZ / "data" / "iq"))
    ap.add_argument("--saida", default=str(RAIZ / "data" / "avaliacao.json"))
    ap.add_argument("--payouts", default=str(RAIZ / "data" / "payouts.json"))
    ap.add_argument("--regras", default="{}")
    ap.add_argument("--progresso", default=str(RAIZ / "data" / "avaliacao_progresso.json"))
    a = ap.parse_args(argv)
    prog = Path(a.progresso)
    try:
        payouts = json.loads(Path(a.payouts).read_text(encoding="utf-8")) if Path(a.payouts).exists() else {}
        res = calcular(Path(a.pasta), payouts, json.loads(a.regras), prog)
        salvar(res, Path(a.saida))
        gravar_progresso(prog, etapa="pronto", feito=1, total=1, msg="avaliação concluída", rodando=False, erro="")
    except Exception as e:  # noqa: BLE001
        gravar_progresso(prog, etapa="erro", rodando=False, erro=str(e))
        raise


if __name__ == "__main__":
    main()
