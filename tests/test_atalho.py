import sys

import app
import instalar


def test_oculto_reabre_o_app_sem_a_flag_so_no_windows():
    cmd = app.relancar_sem_console(["app.py", "--oculto", "--browser"], "win32")
    assert cmd[0] == sys.executable and cmd[1].endswith("app.py") and cmd[2:] == ["--browser"]
    assert app.relancar_sem_console(["app.py"], "win32") is None
    assert app.relancar_sem_console(["app.py", "--oculto"], "linux") is None


def test_atalho_aponta_para_o_python_do_venv_e_nao_para_o_pythonw():
    texto = instalar.instrucoes_atalho_manual()
    assert "pythonw" not in texto and "app.py\" --oculto" in texto
    assert instalar.ICONE.exists()
    if sys.platform != "win32":
        assert instalar.criar_atalho() is None
