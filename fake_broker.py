"""Corretora FALSA + relógios. Permite testar o loop inteiro do bot sem internet
e sem conta:  python main.py --simular  (roda rápido, com relógio acelerado)."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd


class RelogioReal:
    def __init__(self, deslocamento: float = 0.0):
        self.desloc = deslocamento  # hora do servidor - hora local

    def agora(self) -> float:
        return time.time() + self.desloc

    def agora_dt(self) -> datetime:
        return datetime.fromtimestamp(self.agora(), tz=timezone.utc)

    def dormir(self, s: float):
        if s > 0:
            time.sleep(s)


class RelogioFalso(RelogioReal):
    """Tempo simulado: dormir() só avança o ponteiro (teste instantâneo)."""

    def __init__(self, inicio: float):
        self.t = float(inicio)

    def agora(self) -> float:
        return self.t

    def dormir(self, s: float):
        if s > 0:
            self.t += s


class CorretoraFalsa:
    sincrono = True  # resultado calculado na hora (relógio simulado)

    def __init__(self, relogio, ativos=None, seed: int = 3, saldo: float = 1000.0,
                 payout: float = 0.87, minutos: int = 20000, falha_compra_a_cada: int = 0):
        self.relogio = relogio
        self.ativos = ativos or {"EURUSD-op": 1.10, "GBPUSD-op": 1.27, "USDJPY-op": 150.0}
        self._payout = payout
        self._saldo = saldo
        self._ordens: dict[int, dict] = {}
        self._prox = 1
        self.compras = 0
        self.falha_compra_a_cada = falha_compra_a_cada
        rng = np.random.default_rng(seed)
        self.t0 = (int(relogio.agora()) // 60) * 60 - 3000 * 60  # histórico antes do início
        self.precos = {}
        for a, p0 in self.ativos.items():
            dec = 3 if "JPY" in a else 5
            r = rng.standard_normal(minutos + 3000) * 0.00015
            self.precos[a] = np.round(p0 * np.exp(np.cumsum(r)), dec)
        self.tipo = "PRACTICE"

    # interface ---------------------------------------------------------
    def conectar(self, tipo_conta="PRACTICE"):
        self.tipo = tipo_conta

    def conectado(self):
        return True

    def garantir_conexao(self):
        pass

    def ativos_abertos(self):
        return {a: self._payout for a in self.ativos}

    def payout(self, ativo):
        return self._payout

    def hora_servidor(self):
        return self.relogio.agora()

    def _idx(self, ts: float) -> int:
        return int((ts - self.t0) // 60)

    def preco(self, ativo: str, ts: float) -> float:
        return float(self.precos[ativo][min(self._idx(ts), len(self.precos[ativo]) - 1)])

    def velas(self, ativo, tamanho_s=60, quantidade=1000, ate=None):
        agora = self.relogio.agora()
        fim = self._idx(agora)  # vela em formação = fim; fechadas < fim
        ini = max(1, fim - quantidade)
        p = self.precos[ativo]
        c = p[ini:fim]
        o = p[ini - 1:fim - 1]
        idx = pd.to_datetime([(self.t0 + i * 60) for i in range(ini, fim)], unit="s", utc=True)
        hi = np.maximum(o, c)
        lo = np.minimum(o, c)
        return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c,
                             "volume": np.ones(len(c))}, index=idx)

    def comprar(self, ativo, valor, direcao, exp_min):
        self.compras += 1
        if self.falha_compra_a_cada and self.compras % self.falha_compra_a_cada == 0:
            return False, "simulação de recusa"
        agora = self.relogio.agora()
        ent = self.preco(ativo, agora - 60)  # open da vela atual = close da anterior
        vence = (int(agora) // 60 + exp_min) * 60  # fim da vela de vencimento
        oid = self._prox
        self._prox += 1
        self._ordens[oid] = {"ativo": ativo, "valor": valor, "dir": direcao, "ent": ent, "vence": vence}
        self._saldo -= valor
        return True, oid

    def resultado(self, oid, timeout):
        o = self._ordens[oid]
        sai = self.preco(o["ativo"], o["vence"] - 60)  # close da vela de vencimento
        mov = np.sign(sai - o["ent"])
        if mov == 0:
            self._saldo += o["valor"]
            return "equal", 0.0
        ganhou = (mov > 0) == (o["dir"] == "call")
        if ganhou:
            self._saldo += o["valor"] * (1 + self._payout)
            return "win", o["valor"] * self._payout
        return "loose", -o["valor"]

    def humor(self, ativo):
        i = self._idx(self.relogio.agora())
        return float(0.5 + 0.45 * np.sin(i / 7.0 + len(ativo)))

    def saldo(self):
        return self._saldo

    def moeda(self):
        return "BRL"
