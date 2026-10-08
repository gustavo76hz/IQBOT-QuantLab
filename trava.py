"""Trava de instância: impede rodar DOIS bots na mesma conta ao mesmo tempo
(ex.: o CMD com main.py e a interface app.py). Dois logins na mesma conta da IQ
derrubam a sessão um do outro e bagunçam o stop e os limites do dia.

A trava é um arquivo bloqueado pelo sistema operacional (data/bot.lock). Se o
programa fechar ou travar, o Windows libera o bloqueio sozinho.
"""
from __future__ import annotations

import sys
from pathlib import Path


class Trava:
    def __init__(self, caminho: str | Path):
        self.caminho = Path(caminho)
        self._fh = None

    def adquirir(self) -> bool:
        if self._fh:
            return True
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.caminho, "a+b")
        try:
            if sys.platform == "win32":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def liberar(self) -> None:
        if not self._fh:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._fh.close()
        self._fh = None


MENSAGEM = ("Já existe um bot rodando nesta pasta (no CMD ou na interface). "
            "Feche o outro antes: dois bots na mesma conta derrubam a sessão e bagunçam o stop.")
