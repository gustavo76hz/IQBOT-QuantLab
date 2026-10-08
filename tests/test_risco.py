import copy
from datetime import datetime, timedelta, timezone

import configuracao
from risk import FUSO_BR, GestorRisco, tabela_martingale

T0 = datetime(2026, 10, 6, 13, 0, tzinfo=timezone.utc)


def _cfg(**kw):
    c = copy.deepcopy(configuracao.PADRAO["risco"])
    c.update(kw)
    return c


def test_stop_diario(tmp_path):
    g = GestorRisco(_cfg(stop_diario_pct=5), 100, 0, tmp_path)
    assert g.pode_operar(T0)[0]
    g.registrar_resultado("loose", -5, T0)
    ok, motivo = g.pode_operar(T0)
    assert not ok and "stop" in motivo
    assert g.pode_operar(T0 + timedelta(days=1), 95)[0]  # dia seguinte volta


def test_orcamento_total_para_de_vez(tmp_path):
    g = GestorRisco(_cfg(orcamento_teste=10, stop_diario_pct=100), 1000, -8, tmp_path)
    g.registrar_resultado("loose", -5, T0)
    assert not g.pode_operar(T0)[0]
    assert not g.pode_operar(T0 + timedelta(days=3), 1000)[0]


def test_kill_switch(tmp_path):
    g = GestorRisco(_cfg(), 100, 0, tmp_path)
    (tmp_path / "STOP").write_text("x")
    assert not g.pode_operar(T0)[0]


def test_limite_por_hora(tmp_path):
    g = GestorRisco(_cfg(max_operacoes_hora=2), 100, 0, tmp_path)
    g.registrar_abertura(T0)
    g.registrar_abertura(T0)
    assert not g.pode_operar(T0)[0]
    assert g.pode_operar(T0 + timedelta(minutes=61))[0]


def test_martingale_desligado_por_padrao(tmp_path):
    g = GestorRisco(_cfg(), 100, 0, tmp_path)
    g.registrar_resultado("loose", -5, T0)
    assert g.valor_entrada() == g.cfg["entrada"]


def test_martingale_limitado(tmp_path):
    c = _cfg(entrada=2, max_entrada=100, stop_diario_pct=100)
    c["martingale"] = {"ativo": True, "max_gales": 2, "fator": 2.2}
    g = GestorRisco(c, 1000, 0, tmp_path)
    vals = []
    for _ in range(4):
        vals.append(g.valor_entrada())
        g.registrar_resultado("loose", -vals[-1], T0)
    assert vals == [2, 4.4, 9.68, 2]  # volta ao início depois de 2 gales
    assert "NÃO cria vantagem" in tabela_martingale(2, 2.2, 2, 0.85)


def test_pausa_apos_perdas(tmp_path):
    g = GestorRisco(_cfg(max_perdas_seguidas=3, parar_apos_perdas_seguidas=False, stop_diario_pct=100), 1000, 0, tmp_path)
    for _ in range(3):
        g.registrar_resultado("loose", -1, T0)
    assert not g.pode_operar(T0 + timedelta(minutes=5))[0]
    assert g.pode_operar(T0 + timedelta(minutes=31))[0]


def test_stop_loss_2_seguidas_para_o_bot(tmp_path):
    g = GestorRisco(_cfg(entrada=2, max_entrada=2, max_perdas_seguidas=2, parar_apos_perdas_seguidas=True,
                         stop_diario_pct=100), 100, 0, tmp_path)
    g.registrar_resultado("loose", -2, T0)
    assert g.pode_operar(T0)[0] and g.valor_entrada() == 2
    g.registrar_resultado("loose", -2, T0)
    ok, motivo = g.pode_operar(T0 + timedelta(days=2), 96)
    assert not ok and "STOP LOSS" in motivo
    assert g.valor_entrada() == 2  # nunca aumenta


def test_config_padrao_entrada_travada_sem_gale():
    c = configuracao.PADRAO["risco"]
    assert c["entrada"] == c["max_entrada"] and c["martingale"]["ativo"] is False
    assert c["max_perdas_seguidas"] == 2 and c["parar_apos_perdas_seguidas"] is True


def test_stop_2_seguidas_com_resultados_fora_de_ordem(tmp_path):
    """Caso real de 06/10: op3 perde, op4 perde, op5 (aberta junto com a 4) ganha,
    e o resultado da 5 chega ANTES do da 4. Tem que parar mesmo assim."""
    g = GestorRisco(_cfg(max_perdas_seguidas=2, parar_apos_perdas_seguidas=True, stop_diario_pct=100,
                         meta_diaria_pct=1000), 100, 0, tmp_path)
    g.registrar_resultado("loose", -2, T0, op_id=3)
    g.registrar_resultado("win", 1.7, T0, op_id=5)
    assert g.pode_operar(T0)[0]
    g.registrar_resultado("loose", -2, T0, op_id=4)
    ok, motivo = g.pode_operar(T0)
    assert not ok and "STOP LOSS" in motivo


def test_perda_ganho_perda_nao_para(tmp_path):
    g = GestorRisco(_cfg(max_perdas_seguidas=2, parar_apos_perdas_seguidas=True, stop_diario_pct=100,
                         meta_diaria_pct=1000), 100, 0, tmp_path)
    for i, r in enumerate(["loose", "win", "loose"]):
        g.registrar_resultado(r, -2 if r == "loose" else 1.7, T0, op_id=i)
    assert g.pode_operar(T0)[0]


def test_reiniciar_nao_zera_o_dia(tmp_path):
    """Perdeu 4 hoje com saldo inicial 80 -> stop de 6% (4,8) quase batido; reabre o bot,
    perde mais 2 -> tem que parar. Antes da correção, reiniciar zerava o dia."""
    from database import BancoDados
    db = BancoDados(tmp_path / "b.db")
    for i in range(2):
        op = db.abrir_operacao("EURUSD-op", "put", 2, 5, 0.87, "e", False, "REAL",
                               aberta_em=(T0 + timedelta(minutes=i)).isoformat())
        db.fechar_operacao(op, "loose", -2)
    hoje = db.resumo_do_dia("REAL", T0.astimezone(FUSO_BR).date().isoformat())
    assert hoje["lucro"] == -4 and hoje["ops"] == 2
    g = GestorRisco(_cfg(stop_diario_pct=6, max_perdas_seguidas=99), 80 - 4, db.lucro_total("REAL"),
                    tmp_path, hoje=hoje)
    assert g.lucro_dia == -4 and g.saldo_ini_dia == 80
    assert g.pode_operar(T0 + timedelta(minutes=5), 76)[0]
    g.registrar_resultado("loose", -2, T0)
    ok, motivo = g.pode_operar(T0 + timedelta(minutes=10), 74)
    assert not ok and "stop diário" in motivo


def test_meta_zero_nao_para(tmp_path):
    g = GestorRisco(_cfg(meta_diaria_pct=0), 80, 0, tmp_path, hoje={"dia": T0.astimezone(FUSO_BR).date().isoformat(),
                                                                  "lucro": 30.0, "ops": 3})
    assert g.pode_operar(T0)[0]


def test_nao_abre_outra_quando_falta_1_perda_para_o_stop(tmp_path):
    """Caso real de 06/10 21:00: com 1 perda e outra operação ainda aberta, o bot abriu
    mais duas e passou do stop. Agora ele espera o resultado."""
    g = GestorRisco(_cfg(max_perdas_seguidas=2, parar_apos_perdas_seguidas=True, stop_diario_pct=100,
                         max_operacoes_hora=100), 100, 0, tmp_path)
    g.registrar_abertura(T0)
    g.registrar_resultado("loose", -3, T0, op_id=1)
    assert g.pode_operar(T0)[0]                 # 1 perda, nada aberto: pode
    g.registrar_abertura(T0)                    # abre a op 2
    ok, motivo = g.pode_operar(T0)
    assert not ok and "aguardando" in motivo    # não abre a 3ª enquanto a 2ª não fecha
    g.registrar_resultado("win", 2.5, T0, op_id=2)
    assert g.pode_operar(T0)[0]                 # ganhou: libera


def test_espera_antes_do_stop_pode_ser_desligada(tmp_path):
    g = GestorRisco(_cfg(max_perdas_seguidas=2, parar_apos_perdas_seguidas=True, stop_diario_pct=100,
                         max_operacoes_hora=100, esperar_resultado_antes_do_stop=False), 100, 0, tmp_path)
    g.registrar_abertura(T0)
    g.registrar_resultado("loose", -3, T0, op_id=1)
    g.registrar_abertura(T0)                    # 1 perda e uma aberta
    assert g.pode_operar(T0)[0]                 # desligado: não espera
    g.registrar_abertura(T0)
    g.registrar_resultado("loose", -3, T0, op_id=2)
    ok, motivo = g.pode_operar(T0)
    assert not ok and "STOP" in motivo.upper()  # o stop de 2 perdas continua valendo


def test_limite_de_perda_da_sessao_sem_stop_de_seguidas(tmp_path):
    """Sem stop de perdas seguidas (0) e com limite de R$ 25 na sessão: nunca passa de 25."""
    g = GestorRisco(_cfg(max_perdas_seguidas=0, perda_max_sessao=25, stop_diario_pct=100, entrada=3,
                         max_entrada=3, max_operacoes_hora=1000, max_operacoes_dia=10**5, orcamento_teste=1000),
                    100, 30.0, tmp_path)
    perdido, n = 0.0, 0
    while True:
        ok, motivo = g.pode_operar(T0, 100)
        if not ok:
            break
        g.registrar_abertura(T0)
        n += 1
        g.registrar_resultado("loose", -3, T0, op_id=n)
        perdido += 3
    assert n == 8 and perdido == 24 and "LIMITE DA SESSÃO" in motivo   # 8 perdas seguidas não pararam antes
    g2 = GestorRisco(_cfg(max_perdas_seguidas=0, perda_max_sessao=25, stop_diario_pct=100, entrada=3,
                          max_entrada=3, max_operacoes_hora=1000, orcamento_teste=1000), 100, 0, tmp_path)
    g2.lucro_total = -21.0
    g2.registrar_abertura(T0)                        # perdeu 21 e tem 1 aberta: a 2ª passaria de 25
    ok, motivo = g2.pode_operar(T0, 100)
    assert not ok and "aguardando" in motivo
