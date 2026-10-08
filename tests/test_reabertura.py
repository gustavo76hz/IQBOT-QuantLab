from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import reabertura as rb
from database import BancoDados
from executor import Executor
from risk import FUSO_BR

T = datetime(2026, 10, 8, 1, 10, tzinfo=timezone.utc)          # 22:10 em Brasília (7/10)


def _velas(sobe: bool, pausa_atual=True, pausa_anterior=True):
    """~25 h de velas de 1 min com a pausa de 5 min de ontem e (opcional) a de hoje."""
    idx = pd.date_range(T - timedelta(hours=25), T - timedelta(minutes=6), freq="1min", tz="UTC")
    if not pausa_atual:
        idx = pd.date_range(T - timedelta(hours=25), T - timedelta(minutes=1), freq="1min", tz="UTC")
    ontem = T - timedelta(days=1)
    if pausa_anterior:
        idx = idx[(idx < ontem - timedelta(minutes=5)) | (idx >= ontem)]
    rng = np.random.default_rng(1)
    passos = rng.standard_normal(len(idx)) * 1e-4 + (2e-5 if sobe else -2e-5)
    c = 1.30 * np.exp(np.cumsum(passos))
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 0.0}, index=idx)


def test_prever_vai_contra_o_caminho_desde_a_reabertura_anterior():
    p = rb.prever(_velas(sobe=True), T, "AUDUSD-OTC")
    assert p.direcao == -1 and p.z > 10                 # subiu muito desde ontem -> PUT
    p = rb.prever(_velas(sobe=False), T, "AUDUSD-OTC")
    assert p.direcao == 1 and p.z < -10


def test_sem_pausa_ou_sem_reabertura_anterior_nao_entra():
    assert rb.prever(_velas(True, pausa_atual=False), T) is None
    assert rb.prever(_velas(True, pausa_anterior=False), T) is None


def test_janelas_e_escolha():
    agora = datetime(2026, 10, 7, 22, 7, tzinfo=FUSO_BR)
    jan = rb.proximas_janelas(agora, 10)
    assert len(jan) == 1 and jan[0][0] == T and "AUDUSD-OTC" in jan[0][1] and len(jan[0][1]) == 13
    ps = [rb.Previsao("A", 1, -40, 1, 1), rb.Previsao("B", -1, 12, 1, 1), rb.Previsao("C", 1, -5, 1, 1),
          rb.Previsao("D", -1, 60, 1, 1)]
    assert [p.ativo for p in rb.escolher(ps, 10, 2)] == ["D", "A"]


class _Corr:
    """Recusa a compra até a reabertura (instante T) e depois aceita."""
    def __init__(self, relogio):
        self.rel, self.compras = relogio, []

    def comprar(self, ativo, valor, lado, exp):
        if self.rel["t"] < T:
            return False, "asset is not available"
        self.compras.append((ativo, lado, self.rel["t"]))
        return True, len(self.compras)

    def payout(self, a):
        return 0.82

    def resultado(self, ident, timeout):
        return "win", 2.46


class _Risco:
    gale_atual, abertas = 0, 0

    def __init__(self, pode=True):
        self.pode = pode

    def valor_entrada(self):
        return 3.0

    def pode_operar(self, agora, saldo):
        return (True, "ok") if self.pode else (False, "stop diário atingido")

    def registrar_abertura(self, agora):
        self.abertas += 1

    def registrar_resultado(self, *a, **k):
        self.abertas -= 1


def _bot(tmp_path, pode=True):
    relogio = {"t": T - timedelta(seconds=5)}

    class Rel:
        def agora_dt(self):
            return relogio["t"]

    corr = _Corr(relogio)
    db = BancoDados(tmp_path / "t.db")
    risco = _Risco(pode)
    ex = Executor(corr, db, risco, type("N", (), {"enviar": lambda *a: None})(), "REAL", Rel())

    class Bot:
        pass
    b = Bot()
    b.c, b.db, b.risco, b.exec, b.saldo = corr, db, risco, ex, 100.0
    b.cfg = {"ativos": {"max_ativos_simultaneos": 2}}
    b.auto = None

    def dormir(s):
        relogio["t"] += timedelta(seconds=s)
    at = rb.Atirador(b, agora=lambda: relogio["t"], dormir=dormir)
    return at, b, corr, db


def test_tenta_ate_reabrir_compra_no_maximo_2_e_registra(tmp_path):
    at, b, corr, db = _bot(tmp_path)
    prev = [rb.Previsao("AUDUSD-OTC", -1, 40, 1.3, 1.2), rb.Previsao("NZDCAD-OTC", 1, -25, 0.8, 0.9),
            rb.Previsao("EURNZD-OTC", 1, -15, 1.9, 2.0), rb.Previsao("USDHKD-OTC", 1, -3, 7.7, 7.8)]
    at.executar(T, prev)
    b.exec.aguardar_todas(5)
    assert [(a, l) for a, l, _ in corr.compras] == [("AUDUSD-OTC", "put"), ("NZDCAD-OTC", "call")]
    assert all(t >= T for _, _, t in corr.compras)
    ops = db.operacoes_df()
    assert len(ops) == 2 and (ops["resultado"] != "erro").all()          # recusas não viram linhas
    assert ops["obs"].str.contains("reabertura 22:10").all()
    assert ops["estrategia"].str.startswith("reabertura").all()


def test_risco_bloqueia_a_janela(tmp_path):
    at, b, corr, db = _bot(tmp_path, pode=False)
    at.executar(T, [rb.Previsao("AUDUSD-OTC", -1, 40, 1.3, 1.2)])
    assert corr.compras == [] and db.operacoes_df().empty


def test_reserva_das_vagas_perto_da_janela():
    assert rb.em_reserva(T - timedelta(minutes=2)) == T
    assert rb.em_reserva(T + timedelta(seconds=59)) == T
    assert rb.em_reserva(T - timedelta(minutes=3)) is None
    assert rb.em_reserva(T + timedelta(minutes=2)) is None


def test_prever_antes_da_pausa():
    df = _velas(sobe=True, pausa_atual=False)
    df = df[df.index < T - timedelta(minutes=7)]            # baixado 7 min antes, pares ainda abertos
    p = rb.prever(df, T, "X", exigir_pausa=False)
    assert p is not None and p.direcao == -1 and p.z > 10


def test_ciclo_prepara_antes_confirma_pela_lista_e_compra(tmp_path):
    at, b, corr, db = _bot(tmp_path)
    relogio_t = {"t": T - timedelta(minutes=7)}
    at.agora = lambda: relogio_t["t"]
    at.habilitado = lambda: True
    pedidos = []

    def velas(a, tam, n):
        pedidos.append((a, relogio_t["t"]))
        return _velas(sobe=(a != "NZDCAD-OTC"), pausa_atual=False)[lambda d: d.index < relogio_t["t"] - timedelta(minutes=1)]
    corr.velas = velas
    corr.ativos_abertos = lambda: {"AUDUSD-OTC": 0.82}           # AUDUSD não pausou; os outros sim
    executadas = []
    at.executar = lambda t, prev: executadas.append((t, [p.ativo for p in prev]))
    for seg in range(0, 7 * 60, 1):
        relogio_t["t"] = T - timedelta(minutes=7) + timedelta(seconds=seg)
        at.ciclo()
    assert pedidos and all(t <= T - timedelta(minutes=6) for _, t in pedidos)   # velas só antes da pausa
    assert len(executadas) == 1 and executadas[0][0] == T
    assert "AUDUSD-OTC" not in executadas[0][1] and "NZDCAD-OTC" in executadas[0][1]


def test_bot_ligado_tarde_pula_a_janela(tmp_path):
    at, b, corr, db = _bot(tmp_path)
    at.agora = lambda: T - timedelta(minutes=3)
    at.habilitado = lambda: True
    corr.velas = lambda *a: (_ for _ in ()).throw(AssertionError("não pode pedir velas com o par pausado"))
    at.ciclo()
    assert T.isoformat() in at.feitas
