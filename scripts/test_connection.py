"""Testa login, saldo, lista de ativos e velas.  python scripts/test_connection.py"""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from dotenv import load_dotenv  # noqa: E402

from connection import CorretoraIQ  # noqa: E402

load_dotenv(RAIZ / ".env")
c = CorretoraIQ()
print("Conectando na conta PRACTICE (demo) só para teste...")
c.conectar("PRACTICE")
print("OK. Saldo demo:", c.saldo(), c.moeda())
ab = c.ativos_abertos()
print(f"{len(ab)} ativos turbo abertos. Alguns:")
for a, p in sorted(ab.items(), key=lambda x: -x[1])[:15]:
    print(f"  {a:15s} payout {p:.0%}")
alvo = next((a for a in ("EURUSD-op", "EURUSD-OTC", "EURUSD") if a in ab), None)
if alvo:
    df = c.velas(alvo, 60, 5)
    print(f"Últimas velas FECHADAS de {alvo}:\n{df}")
print("Tudo certo.")
