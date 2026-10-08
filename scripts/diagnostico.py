"""Diagnóstico do ambiente: python scripts/diagnostico.py"""
import importlib
import platform
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
ok = True


def linha(nome, bom, detalhe=""):
    global ok
    ok &= bom or nome.startswith("(opcional)")
    print(f"[{'OK ' if bom else 'ERRO' if not nome.startswith('(opcional)') else 'aviso'}] {nome} {detalhe}")


print(f"Python {sys.version.split()[0]} em {platform.system()} {platform.release()}")
linha("Python >= 3.10", sys.version_info >= (3, 10))
if sys.version_info >= (3, 13):
    print("       (3.13 funciona; se algo der erro de compatibilidade, use 3.11)")
linha("venv ativado", sys.prefix != sys.base_prefix, "(rode venv\\Scripts\\activate)")
for mod in ("pandas", "numpy", "yaml", "dotenv", "rich", "requests", "websocket", "iqoptionapi"):
    try:
        m = importlib.import_module(mod)
        linha(f"pacote {mod}", True, getattr(m, "__version__", ""))
    except Exception as e:  # noqa: BLE001
        linha(f"pacote {mod}", False, str(e)[:80])
try:
    import websocket
    linha("websocket-client == 0.56.0", websocket.__version__.startswith("0.56"), websocket.__version__)
except Exception:  # noqa: BLE001
    pass
try:
    import sklearn  # noqa: F401
    linha("(opcional) scikit-learn", True, "(o projeto não depende dele)")
except Exception as e:  # noqa: BLE001
    linha("(opcional) scikit-learn", False, f"bloqueado/ausente: {str(e)[:60]} — sem problema")
linha(".env existe", (RAIZ / ".env").exists(), "(copie .env.exemplo para .env)")
linha("config.yaml existe", (RAIZ / "config.yaml").exists(), "(copie config.exemplo.yaml)")
try:
    import configuracao
    cfg = configuracao.carregar()
    linha("config.yaml válido", True, f"conta={cfg['conta']['tipo']} entrada={cfg['risco']['entrada']}")
except Exception as e:  # noqa: BLE001
    linha("config.yaml válido", False, str(e))
linha("kill switch desligado", not (RAIZ / "STOP").exists(), "(apague o arquivo STOP)")
try:
    import requests
    for nome, url in (("iqoption.com", "https://iqoption.com"),
                      ("Dukascopy", "https://datafeed.dukascopy.com/datafeed/EURUSD/2024/00/02/BID_candles_min_1.bi5"),
                      ("Forex Factory", "https://nfs.faireconomy.media/ff_calendar_thisweek.json")):
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            linha(f"internet: {nome}", r.status_code < 400, f"HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            linha(f"internet: {nome}", False, str(e)[:60])
except ImportError:
    pass
print("\nTudo certo!" if ok else "\nCorrija os itens com ERRO acima.")
