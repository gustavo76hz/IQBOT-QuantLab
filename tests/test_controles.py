"""Controles obrigatórios do pipeline: ruído -> nada; padrão plantado -> acha."""
import pesquisa


def test_controle_aleatorio_nao_aprova_nada(tmp_path):
    r = pesquisa.main(["--controle", "aleatorio", "--familias", "hora", "sequencia", "bollinger",
                       "--saida", str(tmp_path)])
    assert len(r["aprovadas"]) == 0


def test_controle_plantado_acha_hora_14_call(tmp_path):
    r = pesquisa.main(["--controle", "plantado", "--familias", "hora", "--exps", "5", "15",
                       "--saida", str(tmp_path)])
    ap = r["aprovadas"]
    assert ap, "não achou o padrão plantado"
    assert all(x.params["hora"] == 14 and x.lado == 1 for x in ap)
