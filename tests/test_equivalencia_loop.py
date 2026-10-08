"""O loop ao vivo (com corretora FALSA) tem que tomar EXATAMENTE as mesmas decisões
e ter os mesmos resultados que o backtest nos mesmos preços."""
import copy

import pandas as pd

import backtest as bt
import configuracao
import main as m
from database import BancoDados
from fake_broker import CorretoraFalsa, RelogioFalso


def _cfg():
    cfg = copy.deepcopy(configuracao.PADRAO)
    cfg["automacao"] = {"ativo": False}   # testa o caminho sem automação
    cfg["risco"].update(max_operacoes_hora=1000, max_operacoes_dia=100000, stop_diario_pct=100,
                        meta_diaria_pct=10**6, orcamento_teste=10**9, max_perdas_seguidas=10**6)
    cfg["monitor"]["ativo"] = False
    cfg["ativos"].update(lista=["EURUSD-op"], max_ativos_simultaneos=1, nota_minima=0)
    return configuracao.validar(cfg)


def _rodar(tmp_path, familia, params, minutos=900):
    rel = RelogioFalso(1_760_000_000)
    corr = CorretoraFalsa(rel, ativos={"EURUSD-op": 1.1}, seed=5)
    banco = BancoDados(tmp_path / "t.db")
    est = {"familia": familia, "params": params, "ativo": "TODOS", "validada": False, "origem": "teste"}
    est["id"] = "teste"
    bot = m.Bot(_cfg(), corr, rel, banco, "SIMULACAO", est, pasta_stop=tmp_path)
    bot.cache.hist = 1500
    bot.rodar(minutos, atraso=1.5)
    bot.exec.aguardar_todas(5)
    ops = banco.operacoes_df()
    df = corr.velas("EURUSD-op", 60, 6000)
    return ops, df


def test_loop_igual_ao_backtest(tmp_path):
    params = {"n": 3, "lado": -1, "exp": 2}
    ops, df = _rodar(tmp_path, "sequencia", params)
    assert len(ops) > 30
    tr = bt.rodar(df, "sequencia", params)
    ini = pd.Timestamp(ops["aberta_em"].iloc[0]).floor("min")
    fim = pd.Timestamp(ops["aberta_em"].iloc[-1]).floor("min")
    tr = tr[(tr["entrada_hora"] >= ini) & (tr["entrada_hora"] <= fim)]
    vivo = ops.assign(h=pd.to_datetime(ops["aberta_em"]).dt.floor("min"))
    limite = df.index[-1] - pd.Timedelta(minutes=params["exp"])  # backtest precisa da vela de vencimento
    vivo = vivo[vivo["h"] <= limite]
    tr = tr[tr["entrada_hora"] <= limite]
    assert list(vivo["h"]) == list(tr["entrada_hora"])
    assert list(vivo["direcao"]) == ["call" if d > 0 else "put" for d in tr["dir"]]
    res_bt = ["equal" if e else ("win" if w else "loose") for w, e in zip(tr["win"], tr["empate"])]
    assert list(vivo["resultado"]) == res_bt


def test_aleatorio_da_cerca_de_50(tmp_path):
    ops, _ = _rodar(tmp_path, "sequencia", {"n": 2, "lado": 1, "exp": 1}, minutos=2500)
    f = ops[ops["resultado"].isin(["win", "loose"])]
    assert len(f) > 300
    assert 0.42 < (f["resultado"] == "win").mean() < 0.58
