import sys
sys.dont_write_bytecode = True   # testes também não criam __pycache__
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
