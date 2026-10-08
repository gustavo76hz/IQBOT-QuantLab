import copy
import lzma
import os
import struct
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

import configuracao
import dukascopy
import monitor
import news
from connection import velas_para_df
from database import BancoDados


def test_config_exemplo_valida_sem_config_do_usuario():
    cfg = configuracao.validar(copy.deepcopy(configuracao.PADRAO))
    assert cfg["risco"]["martingale"]["ativo"] is False


def test_config_rejeita_entrada_maior_que_max():
    cfg = copy.deepcopy(configuracao.PADRAO)
    cfg["risco"]["entrada"] = 50
    with pytest.raises(configuracao.ErroConfig):
        configuracao.validar(cfg)


def test_banco_abre_fecha_e_arquivo_pode_ser_movido(tmp_path):
    db = BancoDados(tmp_path / "x.db")
    op = db.abrir_operacao("EURUSD-op", "call", 5, 1, 0.87, "e", False, "PRACTICE")
    db.fechar_operacao(op, "win", 4.35)
    db.bloquear_sinal("EURUSD-op", "PUT", "e", "notícia")
    assert db.lucro_total("PRACTICE") == pytest.approx(4.35)
    assert len(db.bloqueados_df()) == 1
    os.replace(tmp_path / "x.db", tmp_path / "y.db")  # nenhuma conexão presa


def test_dukascopy_decodifica():
    recs = b"".join(struct.pack(">Iiiiif", i * 60, 110000 + i, 110005 + i, 109990 + i, 110010 + i, 1.5)
                    for i in range(3))
    df = dukascopy.decodificar(lzma.compress(recs, format=lzma.FORMAT_ALONE), date(2024, 1, 2), "EURUSD")
    assert len(df) == 3
    assert df["open"].iloc[0] == pytest.approx(1.1) and df["close"].iloc[0] == pytest.approx(1.10005)
    assert df.index[1] == pd.Timestamp("2024-01-02 00:01", tz="UTC")
    assert dukascopy.ponto("USDJPY") == 1e3


def test_velas_so_fechadas():
    v = [{"from": 0, "open": 1, "close": 2, "min": 0.5, "max": 2.5, "volume": 3},
         {"from": 60, "open": 2, "close": 2, "min": 2, "max": 2, "volume": 1}]
    df = velas_para_df(v, 60, agora_servidor=90)  # a vela de 60s ainda está em formação
    assert len(df) == 1 and df["low"].iloc[0] == 0.5


def test_noticias_bloqueiam_moeda_certa():
    ev = [{"title": "Non-Farm", "country": "USD", "date": "2026-10-09T08:30:00-04:00", "impact": "High"}]
    f = news.FiltroNoticias({"ativo": True, "minutos_antes": 15, "minutos_depois": 15, "impactos": ["High"],
                             "bloquear_se_falhar": False}, pasta="/tmp/_n", baixar=lambda: ev)
    f.atualizar(forcar=True)
    t = datetime(2026, 10, 9, 12, 25, tzinfo=timezone.utc)
    assert f.bloqueio("EURUSD-op", t) is not None
    assert f.bloqueio("EURJPY-op", t) is None
    assert f.bloqueio("EURUSD-op", t + timedelta(hours=2)) is None


def test_monitor_invalida():
    df = pd.DataFrame({"resultado": ["win"] * 15 + ["loose"] * 35, "payout": 0.87})
    s = monitor.avaliar(df, min_operacoes=40)
    assert s.invalidada
    df2 = pd.DataFrame({"resultado": ["win"] * 26 + ["loose"] * 24, "payout": 0.87})
    assert not monitor.avaliar(df2, min_operacoes=40).invalidada
