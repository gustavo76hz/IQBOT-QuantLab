import sqlite3

import app


def test_fechar_o_app_apaga_so_os_arquivos_da_simulacao(tmp_path):
    d = tmp_path / "data"
    (d / "simulacao" / "humor").mkdir(parents=True)
    (d / "simulacao" / "automacao.json").write_text("{}")
    sqlite3.connect(d / "interface_simulacao.db").close()
    sqlite3.connect(d / "bot.db").close()
    (d / "iq").mkdir()
    app.limpar_simulacao(tmp_path)
    assert not (d / "simulacao").exists() and not (d / "interface_simulacao.db").exists()
    assert (d / "bot.db").exists() and (d / "iq").exists()          # dados reais ficam
    app.limpar_simulacao(tmp_path)                                    # sem nada para apagar: sem erro
