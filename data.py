"""Cache de velas por ativo: carrega o histórico uma vez e depois só busca as
velas novas a cada minuto (mais rápido = entrada mais perto da abertura)."""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger("bot.dados")


class CacheVelas:
    def __init__(self, corretora, tamanho_s: int = 60, historico: int = 1500):
        self.c = corretora
        self.tam = tamanho_s
        self.hist = historico
        self.dfs: dict[str, pd.DataFrame] = {}

    def atualizar(self, ativo: str) -> pd.DataFrame:
        df = self.dfs.get(ativo)
        if df is None or len(df) < 300:
            df = self.c.velas(ativo, self.tam, self.hist)
        else:
            novas = self.c.velas(ativo, self.tam, 10)
            df = pd.concat([df, novas])
            df = df[~df.index.duplicated(keep="last")].sort_index().tail(self.hist)
        self.dfs[ativo] = df
        return df

    def fresco(self, ativo: str, agora_ts: float) -> bool:
        """A última vela fechada é a do minuto anterior? (dados não estão atrasados)"""
        df = self.dfs.get(ativo)
        if df is None or not len(df):
            return False
        ultima = df.index[-1].timestamp()
        abertura_atual = (agora_ts // self.tam) * self.tam
        return abs((abertura_atual - self.tam) - ultima) < 1

    def descartar(self, ativo: str):
        self.dfs.pop(ativo, None)
