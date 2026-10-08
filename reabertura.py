"""Reabertura do OTC: o salto diário depois da pausa de 5 minutos.

O que os dados mostraram (30 dias, 37 pares OTC, 927 reaberturas; veja o estudo):
- todo dia o OTC pausa 5 minutos em horários fixos e, ao voltar, o preço dá um salto grande;
- a primeira vela abre no preço de ANTES da pausa e salta logo de cara;
- o salto desfaz o caminho que o OTC andou desde a reabertura anterior (93,5%); quanto maior esse
  caminho, mais certo (|z| >= 10: de 96% a 100%);
- depois do salto, volta a ser cara ou coroa.

Então a entrada só tem vantagem se a compra sair NO PREÇO ANTIGO, no primeiro instante da reabertura.
Não sabemos se a IQ aceita a compra nesse instante: é isso que o teste ao vivo vai responder. Cada
tentativa fica registrada (quanto tempo depois da reabertura a compra entrou) para medir.

A decisão (direção, ranking) é uma função pura, testável. O 'Atirador' é a thread que, perto de cada
janela, prepara os pares e tenta comprar a partir de alguns segundos antes da reabertura.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from risk import FUSO_BR

log = logging.getLogger("bot.reabertura")

# Horário (Brasília) em que a PRIMEIRA vela depois da pausa abre, e os pares que pausam nele.
# Levantado das velas de 7/09 a 7/10/2026. Antes de entrar, o bot confere nas velas que a pausa
# aconteceu mesmo (se a IQ mudar o horário, ele simplesmente não entra).
JANELAS: dict[str, list[str]] = {
    "22:10": ["AUDCHF", "AUDNZD", "AUDUSD", "CADJPY", "EURNZD", "EURTHB", "GBPNZD", "JPYTHB",
              "NZDCAD", "NZDJPY", "USDHKD", "USDSGD", "USDTHB"],
    "01:05": ["EURGBP", "EURJPY", "GBPJPY", "GBPUSD", "NZDUSD", "USDCHF", "USDINR"],
    "04:05": ["USDBRL", "USDMXN"],
    "05:10": ["AUDCAD", "AUDJPY", "CADCHF", "CHFJPY", "EURAUD", "EURCAD", "EURCHF", "EURUSD",
              "GBPAUD", "GBPCAD", "GBPCHF", "USDCAD", "USDPLN", "USDTRY", "USDZAR"],
}
PADRAO = {"ativo": True, "reserva_antes_s": 120, "reserva_depois_s": 60, "z_min": 10.0, "confirmar_s": 90, "max_entradas_janela": 2, "tentar_de_s": -5.0, "tentar_ate_s": 20.0,
          "intervalo_s": 0.25, "preparar_s": 420.0, "velas": 1600}


def proximas_janelas(agora: datetime, horizonte_min: float = 30) -> list[tuple[datetime, list[str]]]:
    """Janelas (instante da reabertura em UTC, pares) que caem entre agora e agora + horizonte."""
    local = agora.astimezone(FUSO_BR)
    out = []
    for hhmm, pares in JANELAS.items():
        h, m = (int(x) for x in hhmm.split(":"))
        for dia in (0, 1):
            t = (local + timedelta(days=dia)).replace(hour=h, minute=m, second=0, microsecond=0)
            if agora - timedelta(seconds=30) <= t.astimezone(timezone.utc) <= agora + timedelta(minutes=horizonte_min):
                out.append((t.astimezone(timezone.utc), [p + "-OTC" for p in pares]))
    return sorted(out)


def em_reserva(agora: datetime, antes_s: float = 120, depois_s: float = 60) -> datetime | None:
    """Perto de uma janela (2 min antes até 1 min depois), as outras estratégias não entram, para as
    vagas de operação simultânea ficarem livres para a reabertura. Devolve o instante da janela."""
    for t, _ in proximas_janelas(agora - timedelta(seconds=depois_s), (antes_s + depois_s) / 60 + 1):
        if t - timedelta(seconds=antes_s) <= agora <= t + timedelta(seconds=depois_s):
            return t
    return None


@dataclass
class Previsao:
    ativo: str
    direcao: int          # +1 CALL, -1 PUT
    z: float              # quanto o preço andou desde a reabertura anterior, em desvios de 1 min
    preco_antigo: float   # último fechamento antes da pausa
    referencia: float     # fechamento da vela da reabertura anterior
    motivo: str = ""


def prever(df: pd.DataFrame, reabertura: datetime, ativo: str = "", exigir_pausa: bool = True) -> Previsao | None:
    """Direção do salto a partir das velas FECHADAS antes da reabertura.

    Exige a reabertura anterior nas velas (outro buraco de mais de 2 minutos). Com exigir_pausa,
    exige também que a pausa atual já tenha acontecido. Ao vivo as velas são baixadas ANTES da
    pausa (exigir_pausa=False) e a pausa é confirmada depois pela lista de pares abertos — pedir
    velas de um par pausado pode deixar a conexão esperando sem resposta."""
    if df is None or len(df) < 100:
        return None
    df = df[df.index < reabertura]
    if not len(df):
        return None
    if exigir_pausa and reabertura - df.index[-1] < timedelta(minutes=5):
        return None                                     # não pausou: não é reabertura
    if not exigir_pausa and reabertura - df.index[-1] > timedelta(minutes=30):
        return None                                     # velas velhas demais
    buracos = np.where(df.index.to_series().diff() > timedelta(minutes=2))[0]
    if not len(buracos):
        return None
    i0 = int(buracos[-1])                               # vela da reabertura anterior
    ref = float(df["close"].iloc[i0])
    ultimo = float(df["close"].iloc[-1])
    r = np.log(df["close"].iloc[i0:]).diff().dropna()
    sd = float(r.std())
    if not sd or not np.isfinite(sd) or len(r) < 60:
        return None
    z = float(np.log(ultimo / ref) / sd)
    if z == 0:
        return None
    return Previsao(ativo, -1 if z > 0 else 1, z, ultimo, ref)


def escolher(previsoes: list[Previsao], z_min: float, n: int) -> list[Previsao]:
    """As n previsões mais fortes acima do mínimo (nos dados, |z| >= 10 acertou de 96% a 100%)."""
    boas = [p for p in previsoes if p is not None and abs(p.z) >= z_min]
    return sorted(boas, key=lambda p: -abs(p.z))[:n]


class Atirador:
    """Thread que cuida das janelas. Só age enquanto o bot está vivo (passo recente) e o plano OTC
    do bot é a reabertura; respeita o risco (stop de perdas, stop diário, simultâneas)."""

    def __init__(self, bot, cfg: dict | None = None, agora=None, dormir=None):
        self.bot = bot
        self.cfg = {**PADRAO, **(cfg or {})}
        # relógio do bot = hora do SERVIDOR da IQ (o relógio do PC pode estar alguns segundos errado)
        self.agora = agora or (lambda: bot.rel.agora_dt())
        self.dormir = dormir or time.sleep
        self.feitas: set[str] = set()          # janelas já tratadas (instante iso)
        self.preparadas: dict[str, list[Previsao]] = {}
        self.confirmadas: set[str] = set()
        self.ultimo_resumo = ""
        self._thread: threading.Thread | None = None
        self.parar = threading.Event()

    # ---------------------------------------------------------------- estado
    def vivo(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def iniciar(self):
        if not self.vivo():
            self._thread = threading.Thread(target=self._laco, daemon=True, name="reabertura")
            self._thread.start()

    def habilitado(self) -> bool:
        b = self.bot
        if b.auto is None:
            return False
        ligada = bool(self.cfg.get("ativo")) or b.auto.estado_salvo["plano"].get("otc") == "reabertura_otc"
        return ligada and time.time() - getattr(b, "ultimo_passo_real", 0) < 150     # bot rodando

    def status(self) -> str:
        prox = proximas_janelas(self.agora(), 24 * 60)
        if not prox:
            return "Reabertura: sem janela nas próximas 24 h"
        t, pares = prox[0]
        return (f"Reabertura: próxima janela às {t.astimezone(FUSO_BR):%H:%M} ({len(pares)} pares)"
                + (f" · última: {self.ultimo_resumo}" if self.ultimo_resumo else ""))

    # ---------------------------------------------------------------- laço
    def _laco(self):
        while not self.parar.is_set():
            try:
                self.ciclo()
            except Exception as e:  # noqa: BLE001
                log.exception("reabertura: erro no ciclo: %s", e)
            self.dormir(1.0)

    def ciclo(self):
        if not self.habilitado():
            return
        agora = self.agora()
        for t, pares in proximas_janelas(agora, 10):
            chave = t.isoformat()
            if chave in self.feitas:
                continue
            falta = (t - agora).total_seconds()
            # 1) ~7 min antes (os pares ainda estão abertos): baixa as velas e calcula a direção
            if chave not in self.preparadas and falta <= self.cfg["preparar_s"]:
                if falta < 330:                          # já pausou: tarde demais para baixar velas
                    self.feitas.add(chave)
                    log.info("reabertura %s: bot ligou tarde, janela pulada", t.astimezone(FUSO_BR).strftime("%H:%M"))
                    continue
                self.preparadas[chave] = self.preparar(t, pares)
            # 2) ~1,5 min antes: confirma pela lista de pares abertos que a pausa aconteceu
            if chave in self.preparadas and chave not in self.confirmadas and falta <= self.cfg["confirmar_s"]:
                self.preparadas[chave] = self.confirmar(t, self.preparadas[chave])
                self.confirmadas.add(chave)
            # 3) alguns segundos antes: tenta comprar
            if chave in self.confirmadas and falta <= -float(self.cfg["tentar_de_s"]):
                self.feitas.add(chave)
                self.executar(t, self.preparadas.pop(chave))

    def preparar(self, t: datetime, pares: list[str]) -> list[Previsao]:
        prev = []
        for a in pares:
            try:
                df = self.bot.c.velas(a, 60, int(self.cfg["velas"]))
                p = prever(df, t, a, exigir_pausa=False)
            except Exception as e:  # noqa: BLE001
                log.warning("reabertura: velas de %s falharam: %s", a, e)
                p = None
            if p is not None:
                prev.append(p)
        texto = ", ".join(f"{p.ativo[:6]} {'CALL' if p.direcao > 0 else 'PUT'} z={p.z:+.0f}" for p in
                          sorted(prev, key=lambda p: -abs(p.z)))
        log.info("reabertura %s: %d de %d pares com pausa confirmada | %s",
                 t.astimezone(FUSO_BR).strftime("%H:%M"), len(prev), len(pares), texto or "-")
        return prev

    def confirmar(self, t: datetime, previsoes: list[Previsao]) -> list[Previsao]:
        """Fica só com os pares que estão FORA da lista de abertos (pausados) — sinal de que a
        pausa da reabertura aconteceu. Se a lista não vier, não entra (mais seguro)."""
        hora = t.astimezone(FUSO_BR).strftime("%H:%M")
        try:
            abertos = set(self.bot.c.ativos_abertos())
        except Exception as e:  # noqa: BLE001
            log.warning("reabertura %s: não consegui confirmar a pausa (%s) — janela pulada", hora, e)
            return []
        pausados = [p for p in previsoes if p.ativo not in abertos]
        log.info("reabertura %s: pausa confirmada em %d de %d pares", hora, len(pausados), len(previsoes))
        return pausados

    def executar(self, t: datetime, previsoes: list[Previsao]):
        b = self.bot
        maximo = int(b.cfg["ativos"]["max_ativos_simultaneos"])
        n = min(int(self.cfg["max_entradas_janela"]), maximo)
        alvos = escolher(previsoes, float(self.cfg["z_min"]), n)
        hora = t.astimezone(FUSO_BR).strftime("%H:%M")
        if not alvos:
            self.ultimo_resumo = f"{hora}: nenhum par com |z| >= {self.cfg['z_min']:.0f}"
            log.info("reabertura %s: nenhum par forte o bastante", hora)
            return
        from catalogo import obter
        e = obter("reabertura_otc")
        pendentes = {p.ativo: p for p in alvos}
        tentativas = {a: 0 for a in pendentes}
        compradas = []
        fim = t + timedelta(seconds=float(self.cfg["tentar_ate_s"]))
        while pendentes and self.agora() <= fim:
            agora = self.agora()
            pode, motivo = b.risco.pode_operar(agora, b.saldo)
            if not pode:
                log.info("reabertura %s: bloqueada pelo risco (%s)", hora, motivo)
                break
            ocupados = len(b.exec.ocupados)
            for a, p in list(pendentes.items()):
                if ocupados >= maximo:
                    break
                tentativas[a] += 1
                try:
                    payout = float(b.c.payout(a))
                except Exception:  # noqa: BLE001
                    payout = 0.0
                atraso = (self.agora() - t).total_seconds()
                obs = (f"reabertura {hora}: z={p.z:+.1f}, preço antes da pausa {p.preco_antigo:.6g}, "
                       f"tentativa {tentativas[a]}, {atraso:+.2f}s da reabertura")
                op = b.exec.enviar(a, p.direcao, e.exp, payout, e.id, False, registrar_recusa=False, obs=obs)
                if op:
                    ocupados += 1
                    compradas.append(f"{a[:6]} {'CALL' if p.direcao > 0 else 'PUT'} em {atraso:+.1f}s")
                    log.info("reabertura %s: comprou %s (%s)", hora, a, obs)
                    del pendentes[a]
            if pendentes:
                self.dormir(float(self.cfg["intervalo_s"]))
        nao = [f"{a[:6]} ({tentativas[a]} tentativas)" for a in pendentes]
        self.ultimo_resumo = f"{hora}: " + ("; ".join(compradas) or "nenhuma compra") + \
            (f" · sem compra: {', '.join(nao)}" if nao else "")
        log.info("reabertura %s: %s", hora, self.ultimo_resumo)
        b.db.evento("INFO", "reabertura " + self.ultimo_resumo)
