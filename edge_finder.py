"""Edge finder com dados da PRÓPRIA IQ Option (~1 mês por ativo).

    python edge_finder.py --baixar            baixa ~40.000 velas M1 de cada ativo da lista
    python edge_finder.py --baixar --todos-pares   baixa TODOS os pares de moedas -op abertos agora
    python edge_finder.py --baixar --todos-otc     baixa TODOS os pares de moedas -OTC abertos agora
    python edge_finder.py                     roda o pipeline nesses dados

Com só ~1 mês, o poder estatístico é baixo: use para CONFIRMAR o que a pesquisa
com anos de dados (pesquisa.py) encontrou, não para descobrir coisas novas.
O "período novo" aqui são os últimos 7 dias (--dias-novos).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import configuracao

RAIZ = Path(__file__).parent


def baixar(ativos: list[str], velas: int = 40000):
    from dotenv import load_dotenv

    from connection import CorretoraIQ

    load_dotenv(RAIZ / ".env")
    c = CorretoraIQ()
    c.conectar("PRACTICE")
    pasta = RAIZ / "data" / "iq"
    pasta.mkdir(parents=True, exist_ok=True)
    abertos = c.ativos_abertos()
    if ativos in (["TODOS"], ["TODOS-OTC"]):
        import re
        moedas = "USD|EUR|GBP|JPY|AUD|NZD|CAD|CHF|SEK|NOK|SGD|HKD|MXN|TRY|ZAR|PLN|CNH|DKK|HUF|CZK|INR|BRL"
        suf = "-OTC" if ativos == ["TODOS-OTC"] else "-op"
        ativos = sorted(a for a in abertos if re.fullmatch(rf"({moedas})({moedas}){suf}", a))
        print(f"{len(ativos)} pares de moedas {suf} abertos agora: {', '.join(ativos)}")
    for a in ativos:
        if a not in abertos:
            print(f"{a}: fechado agora (tentando mesmo assim)")
        try:
            df = c.velas(a, 60, velas)
            df.to_csv(pasta / f"{a}.csv.gz", index_label="time", compression="gzip")
            print(f"{a}: {len(df):,} velas de {df.index[0]:%Y-%m-%d} a {df.index[-1]:%Y-%m-%d}")
        except Exception as e:  # noqa: BLE001
            print(f"{a}: ERRO {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baixar", action="store_true")
    ap.add_argument("--velas", type=int, default=40000)
    ap.add_argument("--todos-pares", action="store_true", help="todos os pares de moedas -op abertos")
    ap.add_argument("--todos-otc", action="store_true", help="todos os pares de moedas -OTC abertos")
    ap.add_argument("--ativos", nargs="+", default=None, help="ex.: NZDUSD-op USDCHF-op")
    ap.add_argument("--dias-novos", type=int, default=7)
    a, resto = ap.parse_known_args()
    if a.baixar:
        if a.todos_otc:
            lista = ["TODOS-OTC"]
        elif a.todos_pares:
            lista = ["TODOS"]
        elif a.ativos:
            lista = a.ativos
        else:
            lista = configuracao.carregar()["ativos"]["lista"]
        baixar(lista, a.velas)
        return
    import pesquisa
    pesquisa.main(["--fonte", "iq", "--dias-novos", str(a.dias_novos), "--n-min-treino", "150",
                   "--n-min-teste", "100", "--sem-congelar", *resto])


if __name__ == "__main__":
    main()
