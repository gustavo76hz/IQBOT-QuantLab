import subprocess
import sys
import textwrap

from trava import Trava


def test_segunda_instancia_e_bloqueada(tmp_path):
    arq = tmp_path / "bot.lock"
    t = Trava(arq)
    assert t.adquirir()
    # outro processo (como o CMD com main.py) não consegue pegar a trava
    cod = textwrap.dedent(f"""
        import sys; sys.path.insert(0, {str(__import__('pathlib').Path(__file__).resolve().parents[1])!r})
        from trava import Trava
        print('livre' if Trava({str(arq)!r}).adquirir() else 'ocupada')
    """)
    out = subprocess.run([sys.executable, "-c", cod], capture_output=True, text=True).stdout.strip()
    assert out == "ocupada"
    t.liberar()
    out = subprocess.run([sys.executable, "-c", cod], capture_output=True, text=True).stdout.strip()
    assert out == "livre"
