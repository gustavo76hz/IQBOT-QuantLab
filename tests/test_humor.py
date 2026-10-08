import copy
import pathlib

import pandas as pd

import configuracao
import main as m
import strategy as st
from database import BancoDados
from fake_broker import CorretoraFalsa, RelogioFalso


def test_decidir_humor():
    assert st.decidir_humor(0.75, 0.7) == -1   # maioria em CALL -> PUT
    assert st.decidir_humor(0.20, 0.7) == 1    # maioria em PUT -> CALL
    assert st.decidir_humor(0.55, 0.7) == 0
    assert st.decidir_humor(None, 0.7) == 0


def test_loop_humor_opera_e_grava(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "RAIZ", tmp_path)
    cfg = copy.deepcopy(configuracao.PADRAO)
    cfg["automacao"] = {"ativo": False}   # testa o caminho sem automação
    cfg["estrategia"]["manual"] = {"familia": "humor", "params": {"limite": 0.7, "exp": 1}}
    cfg["risco"].update(max_operacoes_hora=1000, max_operacoes_dia=10**5, stop_diario_pct=100,
                        orcamento_teste=10**9, max_perdas_seguidas=10**6)
    cfg["monitor"]["ativo"] = False
    cfg = configuracao.validar(cfg)
    rel = RelogioFalso(1_760_000_000)
    corr = CorretoraFalsa(rel, ativos={"EURUSD-OTC": 1.1, "GBPJPY-OTC": 190.0, "AUDCAD-OTC": 0.9})
    est = m.carregar_estrategia(cfg, tmp_path)
    bot = m.Bot(cfg, corr, rel, BancoDados(tmp_path / "t.db"), "SIM", est, pasta_stop=tmp_path)
    bot.rodar(120, atraso=1.5)
    ops = bot.db.operacoes_df()
    assert len(ops) > 10
    assert set(ops["ativo"]) <= {"EURUSD-OTC", "GBPJPY-OTC", "AUDCAD-OTC"}
    csvs = list((tmp_path / "data" / "humor").glob("humor_*.csv"))
    assert csvs and sum(1 for _ in open(csvs[0])) > 100
    # nunca mais que 3 abertas ao mesmo tempo (exp 1 -> cada minuto no máx. 3 entradas)
    assert ops.groupby("aberta_em").size().max() <= 3


import pytest


@pytest.mark.parametrize("humor_junto", [True, False])
def test_modo_misto_reversao_op_e_humor_otc(tmp_path, monkeypatch, humor_junto):
    monkeypatch.setattr(m, "RAIZ", tmp_path)
    cfg = copy.deepcopy(configuracao.PADRAO)
    cfg["automacao"] = {"ativo": False}   # testa o caminho sem automação
    cfg["estrategia"]["manual"] = {"familia": "humor", "params": {"limite": 0.7, "exp": 1}}
    cfg["estrategia"]["reversao_op"] = {"ativo": True, "horas_brt": [9, 18], "humor_junto": humor_junto,
                                        "params": {"k": 5, "z": 1.0, "regime": "todas", "lado": -1, "exp": 5}}
    cfg["ativos"]["lista"] = ["EURUSD-op", "GBPUSD-op"]
    cfg["ativos"]["max_ativos_simultaneos"] = 2
    cfg["risco"].update(max_operacoes_hora=1000, max_operacoes_dia=10**5, stop_diario_pct=100,
                        orcamento_teste=10**9, max_perdas_seguidas=10**6)
    cfg["monitor"]["ativo"] = False
    cfg = configuracao.validar(cfg)
    rel = RelogioFalso(1_760_022_000)  # 12:00 em Brasília
    corr = CorretoraFalsa(rel, ativos={"EURUSD-op": 1.1, "GBPUSD-op": 1.27, "AUDCAD-OTC": 0.9})
    est = m.carregar_estrategia(cfg, tmp_path)
    bot = m.Bot(cfg, corr, rel, BancoDados(tmp_path / "t.db"), "SIM", est, pasta_stop=tmp_path)
    bot.rodar(120, atraso=1.5)
    ops = bot.db.operacoes_df()
    op = ops[ops["ativo"].str.endswith("-op")]
    otc = ops[ops["ativo"].str.endswith("-OTC")]
    assert len(op) > 3 and op["estrategia"].str.startswith("regime").all()
    if humor_junto:
        assert len(otc) > 3 and otc["estrategia"].str.startswith("humor").all()
    else:  # no horário da reversão o humor só grava
        assert len(otc) == 0
    # nunca mais de 2 operações abertas ao mesmo tempo (max_ativos_simultaneos)
    ab = pd.to_datetime(ops["aberta_em"]); fe = pd.to_datetime(ops["fechada_em"])
    assert max(((ab <= t) & (fe > t)).sum() for t in ab) <= 2
    # o humor continua sendo gravado para TODOS os pares (inclusive -op)
    csv = next((tmp_path / "data" / "humor").glob("humor_*.csv")).read_text()
    assert "EURUSD-op" in csv and "AUDCAD-OTC" in csv


def test_painel_mostra_sinal_bloqueado_por_noticia(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from news import FiltroNoticias
    monkeypatch.setattr(m, "RAIZ", tmp_path)
    cfg = copy.deepcopy(configuracao.PADRAO)
    cfg["automacao"] = {"ativo": False}   # testa o caminho sem automação
    cfg["estrategia"]["manual"] = {"familia": "humor", "params": {"limite": 0.7, "exp": 1}}
    cfg["estrategia"]["reversao_op"] = {"ativo": False}
    cfg["ativos"]["incluir_otc"] = True
    cfg["risco"].update(max_operacoes_hora=1000, max_operacoes_dia=10**5, stop_diario_pct=100,
                        orcamento_teste=10**9, max_perdas_seguidas=10**6)
    cfg["monitor"]["ativo"] = False
    cfg = configuracao.validar(cfg)
    rel = RelogioFalso(1_760_000_000)
    corr = CorretoraFalsa(rel, ativos={"EURUSD-OTC": 1.1, "GBPUSD-OTC": 1.27})
    noticias = FiltroNoticias(dict(cfg["noticias"], ativo=True), tmp_path)
    noticias.atualizar = lambda: None
    quando = datetime.fromtimestamp(1_760_000_000 + 600, tz=timezone.utc)
    noticias.eventos = [{"quando": quando, "moeda": "USD", "impacto": "High", "titulo": "FOMC"}]
    est = m.carregar_estrategia(cfg, tmp_path)
    bot = m.Bot(cfg, corr, rel, BancoDados(tmp_path / "t.db"), "SIM", est, noticias=noticias, pasta_stop=tmp_path)
    estados = []
    orig = bot.passo
    def passo():
        orig()
        estados.append((bot.status, dict(bot.sinais)))
    bot.passo = passo
    bot.rodar(15, atraso=1.5)
    assert bot.db.operacoes_df().empty                       # tudo bloqueado pela notícia
    assert any(s.startswith("SINAL BLOQUEADO — notícia USD: FOMC") for s, _ in estados)
    assert any("BLOQUEADO" in v for _, sn in estados for v in sn.values())
    assert any("até" in s for s, _ in estados)


def _bot_humor(tmp_path, inicio, ativos, uma_por_moeda="humor", reversao=True):
    cfg = copy.deepcopy(configuracao.PADRAO)
    cfg["automacao"] = {"ativo": False}   # testa o caminho sem automação
    cfg["estrategia"]["manual"] = {"familia": "humor", "params": {"limite": 0.7, "exp": 1}}
    cfg["estrategia"]["reversao_op"] = {"ativo": reversao, "horas_brt": [9, 18], "humor_junto": False,
                                        "params": {"k": 5, "z": 1.0, "regime": "todas", "lado": -1, "exp": 5}}
    cfg["ativos"]["lista"] = ["EURUSD-op", "GBPUSD-op"]
    cfg["ativos"]["incluir_otc"] = True
    cfg["ativos"]["max_ativos_simultaneos"] = 2
    cfg["risco"].update(max_operacoes_hora=1000, max_operacoes_dia=10**5, stop_diario_pct=100,
                        orcamento_teste=10**9, max_perdas_seguidas=10**6, uma_por_moeda=uma_por_moeda)
    cfg["monitor"]["ativo"] = False
    cfg = configuracao.validar(cfg)
    rel = RelogioFalso(inicio)
    corr = CorretoraFalsa(rel, ativos=ativos)
    est = m.carregar_estrategia(cfg, tmp_path)
    return m.Bot(cfg, corr, rel, BancoDados(tmp_path / "t.db"), "SIM", est, pasta_stop=tmp_path)


OTC_USD = {"USDBRL-OTC": 5.4, "USDTRY-OTC": 41.0, "USDMXN-OTC": 18.5}


def test_dia_util_com_op_fechado_humor_nao_opera(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "RAIZ", tmp_path)
    bot = _bot_humor(tmp_path, 1_760_022_000, OTC_USD)   # quinta 12h BRT, nenhum -op aberto
    bot.rodar(90, atraso=1.5)
    assert bot.db.operacoes_df().empty
    assert (tmp_path / "data" / "humor").exists()        # mas continua gravando


def test_fim_de_semana_sem_op_humor_opera(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "RAIZ", tmp_path)
    bot = _bot_humor(tmp_path, 1_760_194_800, OTC_USD)   # sábado 12h BRT
    bot.rodar(90, atraso=1.5)
    assert len(bot.db.operacoes_df()) > 3


@pytest.mark.parametrize("regra,max_juntas", [("humor", 1), ("nao", 2)])
def test_uma_operacao_por_moeda(tmp_path, monkeypatch, regra, max_juntas):
    monkeypatch.setattr(m, "RAIZ", tmp_path)
    bot = _bot_humor(tmp_path, 1_760_000_000, OTC_USD, uma_por_moeda=regra, reversao=False)  # 05h53 BRT
    bot.rodar(120, atraso=1.5)
    ops = bot.db.operacoes_df()
    assert len(ops) > 5
    assert ops.groupby("aberta_em").size().max() == max_juntas
