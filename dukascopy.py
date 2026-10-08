"""Download de candles M1 históricos (anos) do datafeed público da Dukascopy.

Uso (no CMD, com o venv ativado):
    python dukascopy.py --pares EURUSD GBPUSD USDJPY AUDUSD USDCAD USDCHF --inicio 2020-01-01

Os arquivos ficam em data/dukascopy/<PAR>/<AAAA-MM>.csv.gz (preço BID, horário UTC).
Se cair a internet, rode de novo: os meses completos já baixados são pulados.

Termos de uso: os dados são da Dukascopy Bank SA, para uso pessoal/pesquisa.
Confira https://www.dukascopy.com antes de qualquer uso comercial.

IMPORTANTE: o que liquida a opção é o preço da IQ, não o da Dukascopy. Antes de
confiar em qualquer resultado, rode `python alinhamento.py` (compara as duas fontes).
"""
from __future__ import annotations

import argparse
import datetime as dt
import lzma
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

URL = "https://datafeed.dukascopy.com/datafeed/{par}/{a}/{m:02d}/{d:02d}/BID_candles_min_1.bi5"
PASTA = Path(__file__).parent / "data" / "dukascopy"
REG = struct.Struct(">Iiiiif")  # tempo(s), open, close, low, high, volume = 24 bytes


def ponto(par: str) -> float:
    par = par.upper()
    if "JPY" in par or par.startswith(("XAU", "XAG")):
        return 1e3
    return 1e5


def decodificar(conteudo: bytes, dia: dt.date, par: str) -> pd.DataFrame:
    if not conteudo:
        return pd.DataFrame()
    bruto = lzma.decompress(conteudo)
    n = len(bruto) // REG.size
    arr = np.frombuffer(bruto[: n * REG.size], dtype=np.dtype([
        ("t", ">u4"), ("o", ">i4"), ("c", ">i4"), ("l", ">i4"), ("h", ">i4"), ("v", ">f4")]))
    p = ponto(par)
    base = pd.Timestamp(dia, tz="UTC")
    df = pd.DataFrame({
        "open": arr["o"] / p, "high": arr["h"] / p, "low": arr["l"] / p,
        "close": arr["c"] / p, "volume": arr["v"].astype(float),
    }, index=base + pd.to_timedelta(arr["t"].astype(np.int64), unit="s"))
    # sanidade da ordem dos campos (high deve ser o maior)
    ruins = (df["high"] < df[["open", "close"]].max(axis=1)) | (df["low"] > df[["open", "close"]].min(axis=1))
    if ruins.mean() > 0.01:
        raise ValueError(f"formato inesperado em {par} {dia}: {ruins.mean():.1%} candles inválidos")
    return df[df["volume"] > 0]  # minutos sem negócio (fim de semana/feriado) saem


def baixar_dia(sessao, par: str, dia: dt.date, tentativas: int = 4) -> pd.DataFrame:
    url = URL.format(par=par.upper(), a=dia.year, m=dia.month - 1, d=dia.day)
    espera = 2.0
    for i in range(tentativas):
        try:
            r = sessao.get(url, timeout=30)
            if r.status_code == 404:
                return pd.DataFrame()
            r.raise_for_status()
            return decodificar(r.content, dia, par)
        except ValueError:
            raise
        except Exception:
            if i == tentativas - 1:
                raise
            time.sleep(espera)
            espera *= 2
    return pd.DataFrame()


def baixar_mes(par: str, ano: int, mes: int, threads: int = 6) -> Path | None:
    import requests

    destino = PASTA / par.upper() / f"{ano:04d}-{mes:02d}.csv.gz"
    hoje = dt.datetime.now(dt.timezone.utc).date()
    mes_completo = (ano, mes) < (hoje.year, hoje.month)
    if destino.exists() and mes_completo:
        return destino
    inicio = dt.date(ano, mes, 1)
    fim = (inicio + dt.timedelta(days=32)).replace(day=1)
    dias = [inicio + dt.timedelta(days=i) for i in range((fim - inicio).days)]
    dias = [d for d in dias if d < hoje]
    if not dias:
        return None
    sessao = requests.Session()
    sessao.headers["User-Agent"] = "Mozilla/5.0 (pesquisa pessoal)"
    with ThreadPoolExecutor(threads) as ex:
        partes = list(ex.map(lambda d: baixar_dia(sessao, par, d), dias))
    partes = [p for p in partes if len(p)]
    if not partes:
        return None
    df = pd.concat(partes).sort_index()
    df = df[~df.index.duplicated()]
    destino.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(destino, index_label="time", compression="gzip")
    return destino


def carregar(par: str, inicio: str | None = None, fim: str | None = None) -> pd.DataFrame:
    arquivos = sorted((PASTA / par.upper()).glob("*.csv.gz"))
    if not arquivos:
        raise FileNotFoundError(f"sem dados de {par} em {PASTA}. Rode dukascopy.py primeiro.")
    df = pd.concat([pd.read_csv(a, index_col="time", parse_dates=["time"]) for a in arquivos])
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[~df.index.duplicated()].sort_index()
    if inicio:
        df = df[df.index >= pd.Timestamp(inicio, tz="UTC")]
    if fim:
        df = df[df.index < pd.Timestamp(fim, tz="UTC")]
    return df


def main():
    ap = argparse.ArgumentParser(description="Baixa candles M1 da Dukascopy")
    ap.add_argument("--pares", nargs="+",
                    default=["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "EURJPY"])
    ap.add_argument("--inicio", default="2020-01-01")
    ap.add_argument("--fim", default=None, help="padrão: hoje")
    ap.add_argument("--threads", type=int, default=6)
    a = ap.parse_args()
    ini = dt.date.fromisoformat(a.inicio)
    fim = dt.date.fromisoformat(a.fim) if a.fim else dt.datetime.now(dt.timezone.utc).date()
    meses = []
    y, m = ini.year, ini.month
    while (y, m) <= (fim.year, fim.month):
        meses.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    for par in a.pares:
        for (y, m) in meses:
            t0 = time.time()
            try:
                p = baixar_mes(par, y, m, a.threads)
                print(f"{par} {y}-{m:02d}: {'ok' if p else 'sem dados'} ({time.time()-t0:.0f}s)")
            except Exception as e:  # segue para o próximo mês
                print(f"{par} {y}-{m:02d}: ERRO {e}")


if __name__ == "__main__":
    main()
