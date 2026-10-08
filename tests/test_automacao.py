"""Testes da automação de pares e estratégias (mercado.py, catalogo.py, avaliador.py, seletor.py)."""
import copy
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

import avaliador
import catalogo
import configuracao
import main as m
from database import BancoDados
from fake_broker import CorretoraFalsa, RelogioFalso
from seletor import Automacao

QUINTA_12H = 1_760_022_000      # 09/10/2025 12:00 em Brasília (dia útil, horário da reversão)
SABADO_12H = 1_760_194_800

OP = {"EURGBP-op": 0.86, "GBPUSD-op": 1.27, "AUDUSD-op": 0.66}
OTC = {"EURUSD-OTC": 1.1, "AUDCAD-OTC": 0.9, "GBPJPY-OTC": 190.0}


class CorretoraFases(CorretoraFalsa):
    """Corretora falsa em que os pares -op fecham numa janela de tempo (como às 18h na IQ)."""
    def __init__(self, rel, fecha_op=None, **kw):
        super().__init__(rel, ativos={**OP, **OTC}, **kw)
        self.fecha_op = fecha_op       # (início, fim) em segundos do relógio

    def ativos_abertos(self):
        t = self.relogio.agora()
        if self.fecha_op and self.fecha_op[0] <= t < self.fecha_op[1]:
            return {a: self._payout for a in self.ativos if a.endswith("-OTC")}
        return super().ativos_abertos()


def _cfg(tmp_path, **auto):
    cfg = copy.deepcopy(configuracao.PADRAO)
    cfg["ativos"]["lista"] = list(OP)
    cfg["ativos"]["max_ativos_simultaneos"] = 2
    cfg["risco"].update(max_operacoes_hora=1000, max_operacoes_dia=10**5, stop_diario_pct=100,
                        orcamento_teste=10**9, max_perdas_seguidas=10**6)
    cfg["monitor"]["ativo"] = False
    cfg["automacao"] = {**cfg["automacao"], "ativo": True, "modo": "semi", "checar_fase_min": 1,
                        "reavaliar_horas": 0, "plano_inicial": {"op": "reversao_5m", "otc": "humor_70"},
                        **auto}
    return configuracao.validar(cfg)


def _bot(tmp_path, cfg, inicio=QUINTA_12H, corr=None):
    rel = RelogioFalso(inicio)
    corr = corr or CorretoraFases(rel)
    corr.relogio = rel
    est = m.carregar_estrategia(cfg, tmp_path)
    return m.Bot(cfg, corr, rel, BancoDados(tmp_path / "t.db"), "SIM", est, pasta_stop=tmp_path)


def _avaliacao(tmp_path, aprovadas: dict):
    """Grava um data/avaliacao.json falso. aprovadas = {(mercado, chave): [pares]}"""
    resumo = {}
    for (merc, ch), pares in aprovadas.items():
        n = 1200 * len(pares)
        resumo.setdefault(merc, {})[ch] = {"pares_aprovados": pares, "n": n, "wins": int(n * 0.55),
                                           "acerto": 0.55 if pares else None,
                                           "ic": [0.535, 0.565] if pares else None, "pares_testados": 3}
    res = {"gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"), "regras": {},
           "combinacoes": [], "resumo": resumo}
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "avaliacao.json").write_text(json.dumps(res), encoding="utf-8")


# ----------------------------------------------------------------------------- catálogo
def test_catalogo_ids_compativeis_com_o_historico():
    assert catalogo.obter("reversao_5m").id == 'regime:{"exp": 5, "k": 5, "lado": -1, "regime": "todas", "z": 1.0}'
    assert catalogo.obter("humor_70").id == 'humor:{"exp": 1, "lado": 1, "limite": 0.7}'
    assert not catalogo.obter("humor_70").validavel and "op" not in catalogo.obter("humor_70").mercados
    assert len({e.chave for e in catalogo.CATALOGO}) == len(catalogo.CATALOGO)


# ----------------------------------------------------------------------------- avaliador
def test_regras_de_aprovacao():
    base = {"mercado": "op", "chave": "reversao_5m", "n": 1000, "wins": 560, "acerto": 0.56,
            "ic": [0.53, 0.59], "p": 1e-6, "m1": 0.55, "m2": 0.57, "u7": 0.55, "n7": 200}
    linhas = [dict(base, ativo="EURGBP-op"),
              dict(base, ativo="GBPUSD-op", n=200),                          # poucas operações
              dict(base, ativo="AUDUSD-op", m1=0.60, m2=0.50),                # instável
              dict(base, ativo="GBPJPY-op", acerto=0.515, p=1e-6),            # abaixo do empate - margem
              dict(base, ativo="EURTHB-op", p=0.4)]                           # pode ser sorte
    r = avaliador.aprovar(linhas, {"EURGBP-op": 0.85}, {})
    assert [x["elegivel"] for x in r] == [True, False, False, False, False]
    assert all(x["motivos"] for x in r[1:])
    res = avaliador.resumir(r)
    assert res["op"]["reversao_5m"]["pares_aprovados"] == ["EURGBP-op"]


def test_ruido_puro_nao_aprova_nada(tmp_path):
    rng = np.random.default_rng(1)
    idx = pd.date_range("2025-09-01", periods=60 * 24 * 32, freq="1min", tz="UTC")
    for nome in ("EURGBP-op", "EURUSD-OTC"):
        c = 1.1 * np.exp(np.cumsum(rng.standard_normal(len(idx)) * 0.0002))
        o = np.r_[c[0], c[:-1]]
        df = pd.DataFrame({"open": o, "high": np.maximum(o, c), "low": np.minimum(o, c), "close": c,
                           "volume": 1.0}, index=idx)
        df.to_csv(tmp_path / f"{nome}.csv.gz", index_label="time", compression="gzip")
    res = avaliador.calcular(tmp_path, {}, {})
    assert len(res["combinacoes"]) > 10
    assert not [x for x in res["combinacoes"] if x["elegivel"]]


# ----------------------------------------------------------------------------- seletor
def test_troca_so_sem_operacao_aberta_e_fica_registrada(tmp_path):
    cfg = _cfg(tmp_path)
    db = BancoDados(tmp_path / "t.db")
    a = Automacao(cfg, tmp_path / "data", db)
    a.aplicar("op", "quadrante_mhi1_minoria")
    agora = datetime.fromtimestamp(QUINTA_12H, timezone.utc)
    assert a.efetivar(sem_operacao_aberta=False, agora=agora) == []
    assert a.estado_salvo["plano"]["op"] == "reversao_5m"
    feitas = a.efetivar(sem_operacao_aberta=True, agora=agora)
    assert feitas and a.estado_salvo["plano"]["op"] == "quadrante_mhi1_minoria"
    tr = db.trocas_df()
    assert len(tr) == 1 and tr.iloc[0]["quem"] == "você"
    # o plano escolhido sobrevive a reiniciar o bot
    assert Automacao(cfg, tmp_path / "data", db).estado_salvo["plano"]["op"] == "quadrante_mhi1_minoria"


@pytest.mark.parametrize("modo,deve_trocar", [("manual", False), ("semi", False), ("auto", True)])
def test_modos(tmp_path, modo, deve_trocar):
    _avaliacao(tmp_path, {("op", "quadrante_mhi1_minoria"): ["EURGBP-op", "GBPUSD-op"]})
    cfg = _cfg(tmp_path, modo=modo)
    a = Automacao(cfg, tmp_path / "data", None)
    agora = datetime.fromtimestamp(QUINTA_12H, timezone.utc)
    recs = a.recomendacoes(agora)
    assert recs and recs[0]["para"] == "quadrante_mhi1_minoria"     # todos os modos RECOMENDAM
    a.efetivar(sem_operacao_aberta=True, agora=agora)
    assert (a.estado_salvo["plano"]["op"] == "quadrante_mhi1_minoria") is deve_trocar
    if modo != "manual":
        # semi/auto: pares de estratégia aprovada seguem a avaliação
        a.estado_salvo["plano"]["op"] = "quadrante_mhi1_minoria"
        a.detector.atualizar({**OP, **OTC})
        assert set(a.plano("op", agora).pares) == {"EURGBP-op", "GBPUSD-op"}


def test_nao_elegivel_nunca_e_ativado_automaticamente(tmp_path):
    _avaliacao(tmp_path, {("op", "quadrante_mhi1_minoria"): []})       # testada e reprovada
    a = Automacao(_cfg(tmp_path, modo="auto"), tmp_path / "data", None)
    agora = datetime.fromtimestamp(QUINTA_12H, timezone.utc)
    assert a.recomendacoes(agora) == []
    a.efetivar(True, agora)
    assert a.estado_salvo["plano"]["op"] == "reversao_5m"
    with pytest.raises(ValueError):
        a.aplicar("op", "quadrante_mhi1_minoria", quem="automático")


def test_ignorar_recomendacao(tmp_path):
    _avaliacao(tmp_path, {("op", "quadrante_mhi1_minoria"): ["EURGBP-op"]})
    a = Automacao(_cfg(tmp_path), tmp_path / "data", None)
    agora = datetime.fromtimestamp(QUINTA_12H, timezone.utc)
    rec = a.recomendacoes(agora)[0]
    a.ignorar(rec["id"], agora)
    assert a.recomendacoes(agora) == []


# ----------------------------------------------------------------------------- bot completo
def test_bot_segue_a_fase_mercado_otc_mercado(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "RAIZ", tmp_path)
    cfg = _cfg(tmp_path, em_teste_otc_horario_comercial=True)
    rel = RelogioFalso(QUINTA_12H)
    corr = CorretoraFases(rel, fecha_op=(QUINTA_12H + 40 * 60, QUINTA_12H + 90 * 60))
    bot = _bot(tmp_path, cfg, corr=corr)
    fases = []
    orig = bot.passo

    def passo():
        orig()
        fases.append(bot.auto.detector.fase)
    bot.passo = passo
    bot.rodar(140, atraso=1.5)
    # histerese: a IQ fecha os -op no minuto 40, mas a fase só muda na 2ª leitura
    i_otc = fases.index("OTC")
    assert 40 <= i_otc <= 43 and fases[i_otc - 1] == "MISTO"
    j = i_otc + fases[i_otc:].index("MISTO")
    assert 90 <= j <= 93                             # volta sozinho quando reabre
    ops = bot.db.operacoes_df()
    t = (pd.to_datetime(ops["aberta_em"]).astype("int64") // 10**9 - QUINTA_12H) // 60
    op_no_fechamento = ops[(t >= i_otc) & (t < 90) & ops["ativo"].str.endswith("-op")]
    assert op_no_fechamento.empty                     # pares fechados não são operados
    assert (ops[ops["ativo"].str.endswith("-OTC")]["estrategia"] == catalogo.obter("humor_70").id).all()
    assert ops["ativo"].str.endswith("-OTC").any() and ops["ativo"].str.endswith("-op").any()
    assert (ops[ops["ativo"].str.endswith("-op")]["estrategia"] == catalogo.obter("reversao_5m").id).all()


def test_sem_estrategia_aprovada_nao_opera_e_explica(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "RAIZ", tmp_path)
    cfg = _cfg(tmp_path, permitir_em_teste=False)
    rel = RelogioFalso(SABADO_12H)
    bot = _bot(tmp_path, cfg, inicio=SABADO_12H, corr=CorretoraFases(rel, fecha_op=(0, 10**12)))
    bot.rodar(30, atraso=1.5)
    assert bot.db.operacoes_df().empty
    assert "Sem estratégia aprovada para OTC" in bot.status
    # mas o humor continua sendo gravado
    assert list((tmp_path / "data" / "humor").glob("humor_*.csv"))


def test_humor_em_teste_nao_opera_no_horario_comercial(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "RAIZ", tmp_path)
    cfg = _cfg(tmp_path)                  # padrão: em_teste_otc_horario_comercial = false
    rel = RelogioFalso(QUINTA_12H)
    bot = _bot(tmp_path, cfg, corr=CorretoraFases(rel, fecha_op=(0, 10**12)))   # só OTC aberto
    bot.rodar(30, atraso=1.5)
    assert bot.db.operacoes_df().empty and "só grava" in bot.status


def test_troca_preserva_stop_e_limites(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "RAIZ", tmp_path)
    cfg = _cfg(tmp_path)
    bot = _bot(tmp_path, cfg)
    risco = bot.risco
    risco.perdas_seguidas, risco.lucro_dia, risco.ops_dia = 1, -3.0, 7
    (tmp_path / "STOP").write_text("x")             # kill switch: nada é operado neste teste
    bot.auto.aplicar("op", "quadrante_mhi1_minoria")
    bot.rodar(3, atraso=1.5)
    assert bot.auto.estado_salvo["plano"]["op"] == "quadrante_mhi1_minoria"
    assert bot.risco is risco and (risco.perdas_seguidas, risco.lucro_dia, risco.ops_dia) == (1, -3.0, 7)
    assert bot.db.operacoes_df().empty


def test_misto_fora_do_horario_da_reversao_usa_o_otc(tmp_path, monkeypatch):
    """18h-24h: pares de mercado ainda abertos, mas a reversão só opera 9h-18h -> o OTC assume."""
    monkeypatch.setattr(m, "RAIZ", tmp_path)
    cfg = _cfg(tmp_path)
    inicio = QUINTA_12H + 7 * 3600                    # 19h em Brasília
    rel = RelogioFalso(inicio)
    bot = _bot(tmp_path, cfg, inicio=inicio, corr=CorretoraFases(rel))
    bot.rodar(40, atraso=1.5)
    ops = bot.db.operacoes_df()
    assert len(ops) and ops["ativo"].str.endswith("-OTC").all()
    assert bot.auto.detector.fase == "MISTO"


def test_troca_de_mercado_parado_entra_na_hora_mesmo_com_otc_operando(tmp_path):
    """Trocar a estratégia dos pares de mercado à noite (só OTC rodando) não espera o OTC parar."""
    cfg = _cfg(tmp_path)
    a = Automacao(cfg, tmp_path / "data", BancoDados(tmp_path / "t.db"))
    agora = datetime.fromtimestamp(QUINTA_12H, timezone.utc)
    a.aplicar("op", "quadrante_mhi1_minoria")
    a.aplicar("otc", "quadrante_mhi1_minoria")
    feitas = a.efetivar(sem_operacao_aberta=False, agora=agora, livres={"op"})
    assert [f["mercado"] for f in feitas] == ["op"]
    assert a.estado_salvo["plano"]["op"] == "quadrante_mhi1_minoria"
    assert a.estado_salvo["plano"]["otc"] == "humor_70"                 # o OTC está operando: espera
    assert set(a.estado_salvo["pendente"]) == {"otc"}


def test_bot_so_otc_troca_plano_de_mercado_sem_esperar(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "RAIZ", tmp_path)
    cfg = _cfg(tmp_path)
    rel = RelogioFalso(SABADO_12H)
    bot = _bot(tmp_path, cfg, inicio=SABADO_12H, corr=CorretoraFases(rel, fecha_op=(0, 10**12)))
    bot.rodar(5, atraso=1.5)                                             # fase OTC, humor operando
    bot.exec.ocupados.add("EURUSD-OTC")                                  # uma operação OTC aberta
    bot.auto.aplicar("op", "quadrante_mhi1_minoria")
    bot.passo()
    assert bot.auto.estado_salvo["plano"]["op"] == "quadrante_mhi1_minoria"


def test_app_com_bot_parado_troca_na_hora(tmp_path, monkeypatch):
    import app
    monkeypatch.setattr(app, "ROOT", tmp_path)
    eng = app.Engine()
    eng.cfg = _cfg(tmp_path)
    eng.mode = "PRACTICE"
    msg = eng.aplicar({"mercado": "op", "chave": "quadrante_mhi1_minoria"})
    assert "trocada" in msg
    salvo = json.loads((tmp_path / "data" / "automacao.json").read_text(encoding="utf-8"))
    assert salvo["plano"]["op"] == "quadrante_mhi1_minoria" and not salvo["pendente"]
    assert len(BancoDados(tmp_path / "data" / "bot.db").trocas_df()) == 1


def test_humor_ignora_leituras_de_0_ou_100(tmp_path, monkeypatch):
    """0% ou 100% = quase ninguém no par; o bot não entra nessas leituras."""
    monkeypatch.setattr(m, "RAIZ", tmp_path)
    cfg = _cfg(tmp_path)
    rel = RelogioFalso(SABADO_12H)
    corr = CorretoraFases(rel, fecha_op=(0, 10**12))
    corr.humor = lambda a: {"EURUSD-OTC": 1.0, "AUDCAD-OTC": 0.0}.get(a, 0.5)
    bot = _bot(tmp_path, cfg, inicio=SABADO_12H, corr=corr)
    bot.rodar(10, atraso=1.5)
    assert bot.db.operacoes_df().empty
    corr.humor = lambda a: {"EURUSD-OTC": 0.85}.get(a, 0.5)            # leitura válida: opera
    bot.rodar(3, atraso=1.5)
    ops = bot.db.operacoes_df()
    assert len(ops) and (ops["ativo"] == "EURUSD-OTC").all() and (ops["direcao"] == "put").all()


def test_app_atualiza_saldo_ao_vivo(monkeypatch):
    import threading
    import time
    import app

    class Corr:
        n = 0

        def saldo(self):
            Corr.n += 1
            return 100.0 + Corr.n

        def ativos_abertos(self):
            return {"EURUSD-OTC": 0.8}

    class B:
        saldo, abertos, auto = 0.0, {}, object()

    eng = app.Engine.__new__(app.Engine)
    eng.stop = threading.Event()
    b = B()
    eng.bot = b
    esperas = iter([False, False])
    monkeypatch.setattr(eng.stop, "wait", lambda s: next(esperas, True))
    monkeypatch.setattr(time, "monotonic", lambda: 10**6 + Corr.n * 31)
    eng._ao_vivo(b, Corr())
    assert b.saldo > 100 and b.abertos == {"EURUSD-OTC": 0.8}


def test_plano_otc_reabertura_nao_opera_fora_das_janelas(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "RAIZ", tmp_path)
    cfg = _cfg(tmp_path, plano_inicial={"op": "reversao_5m", "otc": "reabertura_otc"})
    rel = RelogioFalso(SABADO_12H)
    bot = _bot(tmp_path, cfg, inicio=SABADO_12H, corr=CorretoraFases(rel, fecha_op=(0, 10**12)))
    bot.rodar(10, atraso=1.5)
    assert bot.db.operacoes_df().empty
    assert "AGUARDANDO" in bot.status and "Reabertura" in bot.status
    assert catalogo.obter("reabertura_otc").mercados == ("otc",)


def test_otc_normal_opera_e_para_perto_da_janela_da_reabertura(tmp_path, monkeypatch):
    """Sábado 21:40 -> 22:20: a estratégia do OTC opera normalmente, mas não entra de 22:08 a 22:11."""
    monkeypatch.setattr(m, "RAIZ", tmp_path)
    cfg = _cfg(tmp_path, plano_inicial={"op": "reversao_5m", "otc": "quadrante_mhi1_minoria"})
    cfg["reabertura"] = {"ativo": True}
    inicio = SABADO_12H + 9 * 3600 + 40 * 60
    rel = RelogioFalso(inicio)
    bot = _bot(tmp_path, cfg, inicio=inicio, corr=CorretoraFases(rel, fecha_op=(0, 10**12)))
    bot.rodar(40, atraso=1.5)
    ops = bot.db.operacoes_df()
    assert len(ops) and ops["ativo"].str.endswith("-OTC").all()
    hora = pd.to_datetime(ops["aberta_em"]).dt.tz_convert("America/Sao_Paulo").dt.strftime("%H:%M")
    assert not hora.between("22:08", "22:10").any()


def test_filtro_de_payout_no_otc(tmp_path):
    cfg = _cfg(tmp_path, payout_minimo_otc=0.85, plano_inicial={"op": "reversao_5m", "otc": "quadrante_mhi1_minoria"})
    a = Automacao(cfg, tmp_path / "data", None)
    agora = datetime.fromtimestamp(SABADO_12H, timezone.utc)
    for _ in range(2):
        a.observar({"EURUSD-OTC": 0.82, "USDHKD-OTC": 0.87, "USDINR-OTC": 0.86}, agora)
    pl = a.plano("otc", agora)
    assert set(pl.pares) == {"USDHKD-OTC", "USDINR-OTC"} and pl.pode
    for _ in range(2):
        a.observar({"EURUSD-OTC": 0.82, "AUDCAD-OTC": 0.82}, agora)
    pl = a.plano("otc", agora)
    assert not pl.pode and "payout de 85%" in pl.motivo


def test_confluencia_usa_a_variante_aprovada_em_cada_par(tmp_path, monkeypatch):
    """5+ aprovada em EURGBP e 4+ aprovada em GBPUSD: o plano opera os dois, cada um com a sua."""
    monkeypatch.setattr(m, "RAIZ", tmp_path)
    _avaliacao(tmp_path, {("op", "confluencia_5_m5"): ["EURGBP-op"], ("op", "confluencia_4_m5"): ["GBPUSD-op"]})
    cfg = _cfg(tmp_path, plano_inicial={"op": "confluencia_5_m5", "otc": "reabertura_otc"})
    cfg["ativos"]["lista"] = ["EURGBP-op", "GBPUSD-op", "AUDUSD-op"]
    corr = CorretoraFases(RelogioFalso(QUINTA_12H))
    corr.ativos = {**corr.ativos, "EURGBP-op": 0.86}
    bot = _bot(tmp_path, cfg, corr=corr)
    vistos = []

    def decidir(df, familia, p, *a, **k):
        vistos.append((familia, p["n"]))
        return 1
    monkeypatch.setattr(m.st, "decidir_agora", decidir)
    bot.rodar(8, atraso=1.5)
    pl = bot.auto.plano("op", bot.rel.agora_dt())
    assert pl.status == "aprovada" and set(pl.pares) == {"EURGBP-op", "GBPUSD-op"}
    assert pl.por_par["GBPUSD-op"].chave == "confluencia_4_m5" and "EURGBP-op" not in pl.por_par
    ops = bot.db.operacoes_df()
    est = dict(zip(ops["ativo"], ops["estrategia"]))
    assert est["EURGBP-op"] == catalogo.obter("confluencia_5_m5").id
    assert est["GBPUSD-op"] == catalogo.obter("confluencia_4_m5").id
    assert {("confluencia", 5), ("confluencia", 4)} <= set(vistos)
