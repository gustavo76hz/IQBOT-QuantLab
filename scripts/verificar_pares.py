"""Lista os pares abertos com payout e o PREÇO da última vela fechada.
VERIFICAÇÃO OBRIGATÓRIA: compare cada preço com o que aparece no app da IQ.
python scripts/verificar_pares.py"""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from dotenv import load_dotenv  # noqa: E402

import configuracao  # noqa: E402
from connection import CorretoraIQ  # noqa: E402

load_dotenv(RAIZ / ".env")
cfg = configuracao.carregar()
c = CorretoraIQ()
c.conectar("PRACTICE")
ab = c.ativos_abertos()
print(f"{'ativo':15s} {'aberto':7s} {'payout':7s} {'último close':>14s}  hora (UTC)")
for a in cfg["ativos"]["lista"]:
    if a not in ab:
        print(f"{a:15s} {'NÃO':7s}")
        continue
    try:
        df = c.velas(a, 60, 2)
        print(f"{a:15s} {'sim':7s} {ab[a]:6.0%} {df['close'].iloc[-1]:14.5f}  {df.index[-1]:%H:%M}")
    except Exception as e:  # noqa: BLE001
        print(f"{a:15s} erro: {e}")
print("\nAgora abra o app e confira se os preços batem (diferença de no máximo alguns décimos de pip).")
