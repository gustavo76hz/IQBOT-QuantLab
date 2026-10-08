from mercado import DetectorFase, classificar, separar, tipo_par

OP = {"EURGBP-op": 0.86, "GBPUSD-op": 0.85}
OTC = {"EURUSD-OTC": 0.82, "USDBRL-OTC": 0.82}
OUTROS = {"BTCUSD": 0.8, "AMAZON": 0.8}


def test_tipo_e_separar():
    assert tipo_par("EURGBP-op") == "op" and tipo_par("USDBRL-OTC") == "otc" and tipo_par("BTCUSD") is None
    op, otc = separar({**OP, **OTC, **OUTROS})
    assert set(op) == set(OP) and set(otc) == set(OTC)


def test_classificar():
    assert classificar(2, 0) == "MERCADO" and classificar(2, 3) == "MISTO"
    assert classificar(0, 3) == "OTC" and classificar(0, 0) == "FECHADO"


def test_mercado_otc_mercado_com_histerese():
    d = DetectorFase(leituras_para_trocar=2)
    assert d.atualizar({**OP, **OTC}) == "MISTO" and d.trocou
    # IQ fecha os -op: 1ª leitura ainda não troca (pode ser suspensão de 1 minuto)
    assert d.atualizar(OTC) == "MISTO" and not d.trocou
    assert d.atualizar(OTC) == "OTC" and d.trocou
    assert d.mercados_ativos() == ["otc"]
    # reabre por 1 leitura e some de novo: não troca
    assert d.atualizar({**OP, **OTC}) == "OTC"
    assert d.atualizar(OTC) == "OTC"
    # reabre de verdade
    d.atualizar({**OP, **OTC})
    assert d.atualizar({**OP, **OTC}) == "MISTO" and d.trocou
    assert d.mercados_ativos() == ["op"] and d.mercados_ativos(misto_usa_otc=True) == ["op", "otc"]
    assert d.atualizar({}) == "MISTO" and d.atualizar({}) == "FECHADO" and d.mercados_ativos() == []
