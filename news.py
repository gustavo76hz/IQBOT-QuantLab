"""Filtro de notícias com o calendário semanal do Forex Factory (feed JSON público).
Os eventos também ficam salvos em data/noticias/ para pesquisa futura (deriva após notícia)."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("bot.noticias")

URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


def moedas_do_ativo(ativo: str) -> set[str]:
    base = ativo.split("-")[0].upper()
    return {base[:3], base[3:6]} if len(base) >= 6 else {base}


def interpretar(eventos: list[dict]) -> list[dict]:
    out = []
    for e in eventos:
        try:
            quando = datetime.fromisoformat(str(e["date"])).astimezone(timezone.utc)
        except (KeyError, ValueError):
            continue
        out.append({"quando": quando, "moeda": str(e.get("country", "")).upper(),
                    "impacto": str(e.get("impact", "")), "titulo": str(e.get("title", ""))})
    return out


class FiltroNoticias:
    def __init__(self, cfg: dict, pasta: str | Path = "data/noticias", baixar=None):
        self.cfg = cfg
        self.pasta = Path(pasta)
        self.eventos: list[dict] = []
        self.ultimo = 0.0
        self.falhou = False
        self._baixar = baixar or self._baixar_http

    def _baixar_http(self) -> list[dict]:
        import requests
        r = requests.get(URL, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return r.json()

    def atualizar(self, forcar: bool = False):
        if not self.cfg.get("ativo", True):
            return
        if not forcar and time.time() - self.ultimo < 3600:
            return
        self.ultimo = time.time()
        try:
            bruto = self._baixar()
            self.eventos = interpretar(bruto)
            self.falhou = False
            self.pasta.mkdir(parents=True, exist_ok=True)
            (self.pasta / f"ff_{datetime.now(timezone.utc):%Y%m%d}.json").write_text(
                json.dumps(bruto, ensure_ascii=False), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            self.falhou = True
            log.warning("calendário de notícias indisponível: %s", e)

    def bloqueio(self, ativo: str, agora: datetime, exp_min: int = 1) -> str | None:
        """Retorna o motivo do bloqueio ou None."""
        if not self.cfg.get("ativo", True):
            return None
        if self.falhou and self.cfg.get("bloquear_se_falhar"):
            return "calendário de notícias indisponível"
        moedas = moedas_do_ativo(ativo)
        antes = timedelta(minutes=self.cfg["minutos_antes"] + exp_min)
        depois = timedelta(minutes=self.cfg["minutos_depois"])
        for e in self.eventos:
            if e["impacto"] in self.cfg["impactos"] and e["moeda"] in moedas:
                if e["quando"] - antes <= agora <= e["quando"] + depois:
                    br = timezone(timedelta(hours=-3))  # Brasília
                    fim = (e["quando"] + depois).astimezone(br)
                    return (f"notícia {e['moeda']}: {e['titulo']} às {e['quando'].astimezone(br):%H:%M}"
                            f" — sem entradas em {e['moeda']} até {fim:%H:%M}")
        return None
