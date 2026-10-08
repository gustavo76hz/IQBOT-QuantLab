"""Alertas no Telegram (opcional). Token e chat_id no .env:
TELEGRAM_TOKEN=123:ABC...   TELEGRAM_CHAT_ID=123456789
Como obter: fale com @BotFather (/newbot) e depois com @userinfobot para o chat_id."""
from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger("bot.telegram")


class Notificador:
    def __init__(self, ativo: bool = False):
        self.token = os.getenv("TELEGRAM_TOKEN")
        self.chat = os.getenv("TELEGRAM_CHAT_ID")
        self.ativo = bool(ativo and self.token and self.chat)

    def enviar(self, texto: str):
        if not self.ativo:
            return
        threading.Thread(target=self._enviar, args=(texto,), daemon=True).start()

    def _enviar(self, texto: str):
        try:
            import requests
            requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                          data={"chat_id": self.chat, "text": texto[:4000]}, timeout=10)
        except Exception as e:  # noqa: BLE001
            log.warning("telegram falhou: %s", e)
