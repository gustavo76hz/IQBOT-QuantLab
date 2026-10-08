"""Estratégias (famílias de hipóteses).

REGRA DE OURO: a MESMA função gera o sinal no backtest e ao vivo.
  feats = calcular_features(df, familia, extra)   # causal: linha t só usa dados até t
  sinal = gerar_sinais(feats, familia, params)     # +1 CALL, -1 PUT, 0 nada
Ao vivo usamos apenas a ÚLTIMA linha (último candle FECHADO); a entrada é feita
na abertura do candle seguinte. No backtest a entrada é o open de t+1 e a saída o
close de t+E (E = expiração em minutos).
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

import indicators as ind

# ----------------------------------------------------------------------------
# Features (todas causais)
# ----------------------------------------------------------------------------

def _logret(close: pd.Series, k: int) -> pd.Series:
    return np.log(close / close.shift(k))


def _sigma1(close: pd.Series, janela: int = 1440) -> pd.Series:
    """Desvio padrão (EWM, ~1 dia) do retorno de 1 minuto."""
    r1 = np.log(close / close.shift(1))
    return np.sqrt((r1 ** 2).ewm(span=janela, adjust=False, min_periods=200).mean())


def features_base(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    f = pd.DataFrame(index=df.index)
    s1 = _sigma1(c)
    f["sigma1"] = s1
    for k in (1, 5, 15, 60):
        f[f"z{k}"] = _logret(c, k) / (s1 * np.sqrt(k))
    a14 = ind.atr(df, 14)
    a_longo = ind.true_range(df).ewm(span=1440, adjust=False, min_periods=200).mean()
    f["vol_ratio"] = a14 / a_longo
    _, _, _, f["boll_z"] = ind.bollinger(c, 20, 2.0)
    cor = np.sign(df["close"] - df["open"])
    # tamanho da sequência atual de velas da mesma cor (com sinal)
    grupo = (cor != cor.shift(1)).cumsum()
    f["seq"] = cor * cor.groupby(grupo).cumcount().add(1)
    for i in range(5):  # cores das últimas 5 velas fechadas (c0 = a última)
        f[f"c{i}"] = cor.shift(i)
    # --- votos de reversão para a CONFLUÊNCIA (cada um: +1 CALL, -1 PUT, 0 sem opinião) ---
    o, h, l = df["open"], df["high"], df["low"]
    corpo = (c - o).abs()
    faixa = (h - l).replace(0, np.nan)
    rsi14 = ind.rsi(c, 14)
    sd20 = c.rolling(20, min_periods=20).std()
    m20 = c.rolling(20, min_periods=20).mean()
    r5 = c - c.shift(5)
    z5 = r5 / r5.rolling(300, min_periods=300).std()
    votos = {
        "v_vela": -cor,                                                        # inverter a última vela
        "v_seq3": np.where(f["seq"].abs() >= 3, -cor, 0),                      # 3+ velas da mesma cor
        "v_rsi": np.where(rsi14 < 30, 1, np.where(rsi14 > 70, -1, 0)),         # RSI 14 nos extremos
        "v_boll": np.where(c > m20 + 2 * sd20, -1, np.where(c < m20 - 2 * sd20, 1, 0)),   # fora da Bollinger
        "v_grande": np.where(corpo > 2 * corpo.rolling(20, min_periods=20).mean(), -cor, 0),  # vela grande
        "v_pavio": np.where((h - np.maximum(o, c)) / faixa > 0.6, -1,
                            np.where((np.minimum(o, c) - l) / faixa > 0.6, 1, 0)),           # pavio longo
        "v_z5": np.where(z5 > 1.5, -1, np.where(z5 < -1.5, 1, 0)),             # movimento forte de 5 min
    }
    for nome, v in votos.items():
        f[nome] = pd.Series(v, index=df.index).fillna(0).astype(float)
    vs = f[list(votos)]
    f["conf_n"] = (vs != 0).sum(axis=1)
    f["conf_soma"] = vs.sum(axis=1)
    # hora/minuto da PRÓXIMA vela (a da entrada) — conhecida antecipadamente
    prox = df.index + pd.Timedelta(minutes=1)
    f["hora_entrada"] = prox.hour
    f["min_entrada"] = prox.minute
    f["min_vela"] = df.index.minute
    return f


def features_dxy(df: pd.DataFrame, usd_index: pd.Series, exposicao: int) -> pd.DataFrame:
    """usd_index: log do índice do dólar (alinhado ao df). exposicao: +1 se o par
    sobe quando o dólar sobe (USDJPY), -1 se cai (EURUSD)."""
    f = features_base(df)
    u = usd_index.reindex(df.index).ffill()
    du1 = u.diff()
    su = np.sqrt((du1 ** 2).ewm(span=1440, adjust=False, min_periods=200).mean())
    for k in (1, 3, 5):
        f[f"uz{k}"] = (u - u.shift(k)) / (su * np.sqrt(k))
    f["exposicao"] = exposicao
    return f


# ----------------------------------------------------------------------------
# Regras de sinal por família
# ----------------------------------------------------------------------------

def _sig_hora(f, p):
    return np.where(f["hora_entrada"].to_numpy() == p["hora"], p["lado"], 0)


def _sig_regime(f, p):
    z = f[f"z{p['k']}"].to_numpy()
    vr = f["vol_ratio"].to_numpy()
    reg = p["regime"]
    if reg == "baixa":
        ok = vr < 0.75
    elif reg == "alta":
        ok = vr > 1.4
    elif reg == "media":
        ok = (vr >= 0.75) & (vr <= 1.4)
    else:
        ok = np.isfinite(vr)
    gat = ok & (np.abs(z) > p["z"])
    return np.where(gat, p["lado"] * np.sign(z), 0)


def _sig_choque(f, p):
    z = f["z1"].to_numpy()
    gat = np.abs(z) > p["z"]
    if p.get("programado", False):
        gat &= np.isin(f["min_vela"].to_numpy(), (0, 30))
    return np.where(gat, p["lado"] * np.sign(z), 0)


def _sig_dxy(f, p):
    uz = f[f"uz{p['k']}"].to_numpy()
    pz = f[f"z{p['k']}" if p["k"] in (1, 5) else "z5"].to_numpy()
    s = f["exposicao"].to_numpy()
    # dólar andou forte e o par ainda não acompanhou
    gat = (np.abs(uz) > p["z"]) & (np.abs(pz) < 0.5)
    return np.where(gat, p["lado"] * s * np.sign(uz), 0)


def _sig_bollinger(f, p):
    bz = f["boll_z"].to_numpy()
    gat = np.abs(bz) > p["k"]
    return np.where(gat, p["lado"] * np.sign(bz), 0)


def _sig_seq(f, p):
    s = f["seq"].to_numpy()
    gat = np.abs(s) >= p["n"]
    return np.where(gat, p["lado"] * np.sign(s), 0)


# Táticas de "quadrante" (populares em OTC). Posição na vela = minuto % 5 (0..4).
# Todas com expiração de 1 minuto. 't' = última vela fechada; a entrada é na vela seguinte.
TATICAS = {
    #  nome          : (posição da vela de ENTRADA, função das cores c0..c4 -> direção)
    "mhi1_minoria":  (0, lambda c: -np.sign(c[0] + c[1] + c[2])),  # 3 últimas do quadrante -> minoria
    "mhi1_maioria":  (0, lambda c: np.sign(c[0] + c[1] + c[2])),
    "mhi2_minoria":  (1, lambda c: -np.sign(c[1] + c[2] + c[3])),  # mesma análise, entra na 2ª vela
    "mhi3_minoria":  (2, lambda c: -np.sign(c[2] + c[3] + c[4])),  # entra na 3ª vela
    "milhao_maioria": (0, lambda c: np.sign(c[0] + c[1] + c[2] + c[3] + c[4])),  # 5 velas do quadrante
    "milhao_minoria": (0, lambda c: -np.sign(c[0] + c[1] + c[2] + c[3] + c[4])),
    "padrao23":      (1, lambda c: np.sign(c[0])),   # vela 2 = cor da vela 1
    "tres_mosqueteiros": (3, lambda c: np.sign(c[0])),  # vela 4 = cor da vela 3
    "torres_gemeas": (3, lambda c: np.sign(c[2])),   # vela 4 = cor da vela 1
}


def _sig_quadrante(f, p):
    pos, regra = TATICAS[p["tatica"]]
    c = [f[f"c{i}"].to_numpy() for i in range(5)]
    d = regra(c)
    return np.where(f["min_entrada"].to_numpy() % 5 == pos, d, 0)


def _sig_confluencia(f, p):
    """Entra só quando pelo menos n dos 7 sinais de reversão concordam e nenhum discorda."""
    n_ativos = f["conf_n"].to_numpy()
    soma = f["conf_soma"].to_numpy()
    gat = (n_ativos >= p["n"]) & (np.abs(soma) == n_ativos)
    return np.where(gat, np.sign(soma), 0)


def decidir_humor(pct_call: float | None, limite: float) -> int:
    """'Contra a multidão': se >= limite dos traders estão em CALL -> PUT (-1);
    se >= limite estão em PUT (CALL <= 1-limite) -> CALL (+1)."""
    if pct_call is None or not np.isfinite(pct_call):
        return 0
    if pct_call >= limite:
        return -1
    if pct_call <= 1 - limite:
        return 1
    return 0


def _sig_humor(f, p):  # sem histórico de humor: não dá para gerar sinais em backtest
    return np.zeros(len(f), dtype=int)


@dataclass
class Familia:
    nome: str
    descricao: str
    regra: Callable
    grade: dict = field(default_factory=dict)  # parâmetros (sem 'lado' e 'exp')
    precisa_dxy: bool = False

    def combinacoes(self):
        chaves = list(self.grade)
        for vals in itertools.product(*[self.grade[k] for k in chaves]):
            yield dict(zip(chaves, vals))


FAMILIAS: dict[str, Familia] = {
    "hora": Familia("hora", "Direção fixa por hora do dia (sessões), escolhida no treino",
                    _sig_hora, {"hora": list(range(24))}),
    "regime": Familia("regime", "Momentum/reversão após movimento forte, por regime de volatilidade",
                      _sig_regime, {"k": [5, 15, 60], "z": [1.5, 2.5, 3.5],
                                    "regime": ["baixa", "media", "alta", "todas"]}),
    "choque": Familia("choque", "Deriva/reversão após candle de choque (proxy de notícia; :00/:30)",
                      _sig_choque, {"z": [4.0, 6.0], "programado": [True, False]}),
    "dxy": Familia("dxy", "Índice do dólar sintético lidera o par (par atrasado alcança)",
                   _sig_dxy, {"k": [1, 3, 5], "z": [1.5, 2.5]}, precisa_dxy=True),
    "bollinger": Familia("bollinger", "Reversão em Bollinger (controle: já falhou na IQ)",
                         _sig_bollinger, {"k": [2.0, 2.5, 3.0]}),
    "sequencia": Familia("sequencia", "N velas da mesma cor seguidas",
                         _sig_seq, {"n": [3, 4, 5, 6]}),
    "humor": Familia("humor", "Contra a multidão: opera contra a maioria dos traders (humor da IQ) — só ao vivo",
                     _sig_humor, {"limite": [0.6, 0.7, 0.8]}),
    "confluencia": Familia("confluencia", "Confluência: n ou mais sinais de reversão concordando (RSI, Bollinger, "
                           "vela, sequência, vela grande, pavio, movimento de 5 min)",
                           _sig_confluencia, {"n": [3, 4, 5]}),
    "quadrante": Familia("quadrante", "Táticas de quadrante de 5 min (MHI, Milhão, Padrão 23...) - exp 1 min",
                         _sig_quadrante, {"tatica": list(TATICAS)}),
}


def calcular_features(df: pd.DataFrame, familia: str, usd_index: pd.Series | None = None,
                      exposicao: int = 0) -> pd.DataFrame:
    if FAMILIAS[familia].precisa_dxy:
        if usd_index is None:
            raise ValueError("família dxy precisa do índice do dólar")
        return features_dxy(df, usd_index, exposicao)
    return features_base(df)


def gerar_sinais(feats: pd.DataFrame, familia: str, params: dict) -> np.ndarray:
    s = FAMILIAS[familia].regra(feats, params)
    s = np.nan_to_num(np.asarray(s, dtype=float), nan=0.0).astype(np.int8)
    return s


def decidir_agora(df_fechados: pd.DataFrame, familia: str, params: dict,
                  usd_index: pd.Series | None = None, exposicao: int = 0) -> int:
    """Usada AO VIVO: decisão para a vela que vai abrir agora (+1/-1/0)."""
    if len(df_fechados) < 300:
        return 0
    f = calcular_features(df_fechados, familia, usd_index, exposicao)
    s = gerar_sinais(f.iloc[-1:], familia, params)
    exp = int(params.get("exp", 1))
    if exp >= 15:
        prox = df_fechados.index[-1] + pd.Timedelta(minutes=1)
        if prox.minute % 15 != 0:
            return 0
    return int(s[-1])


# Exposição de cada par ao dólar (para o índice sintético)
EXPOSICAO_USD = {"EURUSD": -1, "GBPUSD": -1, "AUDUSD": -1, "NZDUSD": -1,
                 "USDJPY": 1, "USDCAD": 1, "USDCHF": 1}


def indice_usd(closes: dict[str, pd.Series], excluir: str | None = None) -> pd.Series:
    """Log do índice do dólar: média de exposicao*log(preço) dos pares disponíveis."""
    partes = []
    for par, s in closes.items():
        base = par.replace("-op", "").replace("-OTC", "").upper()
        if base == (excluir or "").upper() or base not in EXPOSICAO_USD:
            continue
        partes.append(EXPOSICAO_USD[base] * np.log(s))
    if not partes:
        raise ValueError("nenhum par para montar o índice do dólar")
    return pd.concat(partes, axis=1).ffill().mean(axis=1)
