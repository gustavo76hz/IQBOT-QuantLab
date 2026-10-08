"""ARQUIVA (não apaga) o banco de operações e começa um histórico novo.
Use quando trocar de estratégia, para o monitor medir só a nova.
python scripts/zerar_historico.py"""
import shutil
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
db = RAIZ / "data" / "bot.db"
if not db.exists():
    print("Não há histórico para arquivar.")
    sys.exit(0)
destino = RAIZ / "data" / "arquivo"
destino.mkdir(parents=True, exist_ok=True)
alvo = destino / f"bot_{datetime.now():%Y%m%d_%H%M%S}.db"
try:
    shutil.move(str(db), str(alvo))
except PermissionError:
    print("O arquivo está em uso. Feche o bot (e o DB Browser, se aberto) e tente de novo.")
    sys.exit(1)
print(f"Histórico arquivado em {alvo}. ATENÇÃO: o orçamento de teste também recomeça do zero.")
