import shutil
from pathlib import Path

import pytest

import configuracao
from risk import GestorRisco
from tests.test_risco import T0, _cfg

RAIZ = Path(__file__).resolve().parents[1]


def _copia(tmp_path):
    p = tmp_path / "config.yaml"
    shutil.copy(RAIZ / "config.exemplo.yaml", p)
    return p


def test_salvar_pela_tela_mantem_comentarios_e_valida(tmp_path):
    p = _copia(tmp_path)
    antes = p.read_text(encoding="utf-8")
    cfg = configuracao.salvar_campos({"entrada": "4,50", "perda_dia": 30, "payout_otc": 0.85,
                                      "perdas_seguidas": 0, "reabertura": False}, p)
    texto = p.read_text(encoding="utf-8")
    assert cfg["risco"]["entrada"] == 4.5 and cfg["risco"]["max_entrada"] == 4.5
    assert cfg["risco"]["stop_diario_valor"] == 30 and cfg["automacao"]["payout_minimo_otc"] == 0.85
    assert cfg["reabertura"]["ativo"] is False and cfg["risco"]["max_perdas_seguidas"] == 0
    assert "# valor por operação" in texto                       # comentários continuam
    assert len(texto.splitlines()) == len(antes.splitlines())     # só trocou valores
    assert configuracao.valores_tela(configuracao.carregar(p))["entrada"] == 4.5


@pytest.mark.parametrize("campo,valor", [("entrada", 0), ("stop_dia_pct", 150), ("simultaneas", 0),
                                         ("entrada", "abc"), ("inventado", 1)])
def test_valor_invalido_nao_grava_nada(tmp_path, campo, valor):
    p = _copia(tmp_path)
    antes = p.read_text(encoding="utf-8")
    with pytest.raises(configuracao.ErroConfig):
        configuracao.salvar_campos({campo: valor}, p)
    assert p.read_text(encoding="utf-8") == antes


def test_perda_maxima_do_dia_em_reais(tmp_path):
    g = GestorRisco(_cfg(stop_diario_valor=10, stop_diario_pct=100, max_perdas_seguidas=0, entrada=3,
                         max_entrada=3, max_operacoes_hora=1000, orcamento_teste=1000), 100, 0, tmp_path)
    n = 0
    while g.pode_operar(T0, 100)[0]:
        n += 1
        g.registrar_abertura(T0)
        g.registrar_resultado("loose", -3, T0, op_id=n)
    assert n == 3 and g.lucro_dia == -9                           # a 4ª passaria de 10
    assert "perda máxima do dia" in g.pode_operar(T0, 100)[1]


def test_app_salvar_config_pede_a_palavra(tmp_path, monkeypatch):
    import app
    p = _copia(tmp_path)
    monkeypatch.setattr(app, "ROOT", tmp_path)
    eng = app.Engine()
    with pytest.raises(ValueError):
        eng.salvar_config({"valores": {"entrada": 5}, "confirmation": "errado"})
    frase = eng.cfg["conta"]["frase_confirmacao_real"]
    msg = eng.salvar_config({"valores": {"entrada": 5}, "confirmation": frase})
    assert "Iniciar" in msg and eng.cfg["risco"]["entrada"] == 5
    assert configuracao.carregar(p)["risco"]["entrada"] == 5
