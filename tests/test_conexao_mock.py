"""Exercita connection.py com uma iqoptionapi FALSA (mesmos nomes/formatos da 7.1.1)."""
import sys
import time
import types

import pytest


class _Api:
    def __init__(self):
        self.socket_option_closed = {}
        self.traders_mood = {}

    def subscribe_Traders_mood(self, aid, instrument):
        self.traders_mood[aid] = 0.73


class IQFalso:
    def __init__(self, email, senha):
        self.api = _Api()
        self.modo = None

    def connect(self):
        return True, None

    def change_balance(self, m):
        self.modo = m

    def get_balance_mode(self):
        return self.modo

    def check_connect(self):
        return True

    def get_server_timestamp(self):
        return time.time()

    def get_all_init_v2(self):
        return {"turbo": {"actives": {
            "76": {"name": "front.EURUSD-op", "enabled": True, "is_suspended": False,
                   "option": {"profit": {"commission": 13}}},
            "77": {"name": "front.GBPUSD-op", "enabled": True, "is_suspended": True,
                   "option": {"profit": {"commission": 15}}}}}}

    def get_candles(self, ativo, tam, n, fim):
        from iqoptionapi import constants
        assert ativo in constants.ACTIVES  # o registro do id tem que ter acontecido
        base = int(fim // 60) * 60
        return [{"from": base - 60 * i, "open": 1.1, "close": 1.1001, "min": 1.0999, "max": 1.1002,
                 "volume": 5} for i in range(n)][::-1]

    def buy(self, valor, ativo, lado, exp):
        self.api.socket_option_closed[42] = {"msg": {"win": "win", "sum": valor, "win_amount": valor * 1.87}}
        return True, 42

    def get_balance(self):
        return 123.0

    def get_currency(self):
        return "BRL"


@pytest.fixture
def lib(monkeypatch):
    pkg = types.ModuleType("iqoptionapi")
    pkg.__path__ = []
    const = types.ModuleType("iqoptionapi.constants")
    const.ACTIVES = {"EURUSD": 1}
    sa = types.ModuleType("iqoptionapi.stable_api")
    sa.IQ_Option = IQFalso
    pkg.constants = const
    monkeypatch.setitem(sys.modules, "iqoptionapi", pkg)
    monkeypatch.setitem(sys.modules, "iqoptionapi.constants", const)
    monkeypatch.setitem(sys.modules, "iqoptionapi.stable_api", sa)
    return const


def test_fluxo_completo(lib):
    from connection import CorretoraIQ
    c = CorretoraIQ("a@b.c", "x")
    c.conectar("REAL")
    ab = c.ativos_abertos()
    assert ab == {"EURUSD-op": pytest.approx(0.87)}  # suspenso fica de fora
    assert lib.ACTIVES["EURUSD-op"] == 76 and lib.ACTIVES["GBPUSD-op"] == 77
    df = c.velas("EURUSD-op", 60, 1500)  # pagina (>1000)
    assert len(df) == 1500 and df.index.is_monotonic_increasing
    assert df.index[-1].timestamp() + 60 <= time.time()  # só fechadas
    ok, oid = c.comprar("EURUSD-op", 5, "call", 1)
    assert ok and c.resultado(oid, 5) == ("win", pytest.approx(4.35))
    assert c.saldo() == 123.0
    assert c.humor("EURUSD-op") == 0.73


def test_timeout_nao_trava():
    from connection import TempoEsgotado, chamar
    with pytest.raises(TempoEsgotado):
        chamar(time.sleep, 5, timeout=0.2)
