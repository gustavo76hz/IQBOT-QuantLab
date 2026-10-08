"""Abre o IQBOT Quant Lab sem janela de CMD — dê DOIS CLIQUES neste arquivo.

O jeito mais fácil é o atalho "IQBOT Quant Lab" que o instalar.py cria na Área de Trabalho.
Este arquivo faz o mesmo: abre o app com o python.exe do venv, escondido. Não usa o
pythonw.exe, que o Smart App Control do Windows 11 pode bloquear.
"""
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
WINDOWS = sys.platform == "win32"
PY = RAIZ / "venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")


def avisar(texto: str) -> None:
    try:
        import tkinter
        from tkinter import messagebox
        raiz = tkinter.Tk()
        raiz.withdraw()
        messagebox.showwarning("IQBOT Quant Lab", texto)
        raiz.destroy()
    except Exception:  # noqa: BLE001
        print(texto)


if not PY.exists():
    avisar("O app ainda não foi instalado.\n\nDê dois cliques em instalar.py primeiro.")
    sys.exit(1)

extra = {"creationflags": 0x08000000} if WINDOWS else {}          # CREATE_NO_WINDOW
subprocess.Popen([str(PY), str(RAIZ / "app.py")], cwd=str(RAIZ), close_fds=True, **extra)
