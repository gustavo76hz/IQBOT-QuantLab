"""Instalador do IQBOT Quant Lab — dê DOIS CLIQUES neste arquivo (precisa do Python instalado).

Cria o ambiente virtual (pasta venv), instala as bibliotecas, prepara os arquivos de
configuração e cria o atalho "IQBOT Quant Lab" na Área de Trabalho.

Pensado para o Windows 11 com o Controle Inteligente de Aplicativos (Smart App Control)
ligado: só usa programas assinados (o python.exe do python.org e o do venv). Nada de .bat,
.exe próprio ou pythonw.exe, que o Windows bloqueia em arquivos baixados sem assinatura.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
VENV = RAIZ / "venv"
WINDOWS = sys.platform == "win32"
PY_VENV = VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")
APP = RAIZ / "app.py"
ICONE = RAIZ / "interface" / "assets" / "anjo.ico"
NOME_ATALHO = "IQBOT Quant Lab.lnk"
CREATE_NO_WINDOW = 0x08000000

# A biblioteca da IQ Option não está no PyPI: o pip baixa direto do repositório dela no GitHub.
IQOPTIONAPI = "https://github.com/iqoptionapi/iqoptionapi/archive/refs/heads/master.zip"


def passo(texto: str) -> None:
    print(f"\n=== {texto} ===", flush=True)


def rodar(*args) -> None:
    r = subprocess.run([str(a) for a in args], cwd=RAIZ)
    if r.returncode != 0:
        raise RuntimeError("o comando acima falhou: " + " ".join(str(a) for a in args))


# ---------------------------------------------------------------- atalho na Área de Trabalho
def pasta_desktop() -> Path:
    """Área de Trabalho de verdade (no Windows 11 ela costuma estar dentro do OneDrive)."""
    if WINDOWS:
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort),
                            ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]
            desktop = GUID()
            ctypes.oledll.ole32.CLSIDFromString("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
                                                ctypes.byref(desktop))
            caminho = wintypes.LPWSTR()
            ctypes.oledll.shell32.SHGetKnownFolderPath(ctypes.byref(desktop), 0, None,
                                                       ctypes.byref(caminho))
            try:
                return Path(caminho.value)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(caminho)
        except Exception:  # noqa: BLE001
            pass
    return Path.home() / "Desktop"


def _atalho_com(lnk: Path, alvo: Path, args: str, pasta: Path, icone: Path, descricao: str) -> None:
    """Cria o .lnk pela interface IShellLinkW do próprio Windows (só ctypes, sem scripts)."""
    import ctypes
    from ctypes import POINTER, WINFUNCTYPE, byref, c_int, c_void_p, c_wchar_p, HRESULT

    class GUID(ctypes.Structure):
        _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort),
                    ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]

    ole32 = ctypes.oledll.ole32

    def guid(texto: str) -> GUID:
        g = GUID()
        ole32.CLSIDFromString(texto, byref(g))
        return g

    def metodo(obj: c_void_p, indice: int, *tipos, retorno=HRESULT):
        tabela = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
        return WINFUNCTYPE(retorno, c_void_p, *tipos)(tabela[indice])

    try:
        ole32.CoInitialize(None)
    except OSError:
        pass                                    # COM já iniciado nesta thread
    link = c_void_p()
    ole32.CoCreateInstance(byref(guid("{00021401-0000-0000-C000-000000000046}")), None, 1,
                           byref(guid("{000214F9-0000-0000-C000-000000000046}")), byref(link))
    arquivo = c_void_p()
    try:
        metodo(link, 20, c_wchar_p)(link, str(alvo))                    # SetPath
        metodo(link, 11, c_wchar_p)(link, args)                         # SetArguments
        metodo(link, 9, c_wchar_p)(link, str(pasta))                    # SetWorkingDirectory
        metodo(link, 7, c_wchar_p)(link, descricao)                     # SetDescription
        metodo(link, 15, c_int)(link, 7)                                # SetShowCmd: minimizado
        if icone.exists():
            metodo(link, 17, c_wchar_p, c_int)(link, str(icone), 0)     # SetIconLocation
        metodo(link, 0, POINTER(GUID), POINTER(c_void_p))(              # QueryInterface
            link, byref(guid("{0000010B-0000-0000-C000-000000000046}")), byref(arquivo))
        metodo(arquivo, 6, c_wchar_p, c_int)(arquivo, str(lnk), 1)      # IPersistFile.Save
    finally:
        for obj in (arquivo, link):
            if obj.value:
                metodo(obj, 2, retorno=ctypes.c_ulong)(obj)             # Release


def _atalho_powershell(lnk: Path, alvo: Path, args: str, pasta: Path, icone: Path, descricao: str) -> None:
    def q(texto) -> str:
        return "'" + str(texto).replace("'", "''") + "'"
    comando = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({q(lnk)});"
               f"$s.TargetPath={q(alvo)};$s.Arguments={q(args)};$s.WorkingDirectory={q(pasta)};"
               f"$s.Description={q(descricao)};$s.WindowStyle=7;"
               + (f"$s.IconLocation={q(str(icone) + ',0')};" if icone.exists() else "")
               + "$s.Save()")
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", comando],
                   check=True, capture_output=True, creationflags=CREATE_NO_WINDOW)


def criar_atalho() -> Path | None:
    """Atalho na Área de Trabalho -> venv\\Scripts\\python.exe app.py --oculto (ícone do anjo)."""
    if not WINDOWS:
        return None
    lnk = pasta_desktop() / NOME_ATALHO
    dados = (lnk, PY_VENV, f'"{APP}" --oculto', RAIZ, ICONE, "Abre o IQBOT Quant Lab")
    for criar in (_atalho_com, _atalho_powershell):
        try:
            criar(*dados)
            if lnk.exists():
                return lnk
        except Exception as e:  # noqa: BLE001
            print(f"(tentativa de criar o atalho falhou: {e})")
    return None


def instrucoes_atalho_manual() -> str:
    return ("Crie o atalho à mão: botão direito na Área de Trabalho > Novo > Atalho, e cole:\n"
            f'    "{PY_VENV}" "{APP}" --oculto\n'
            f"Depois, em Propriedades do atalho: Iniciar em = {RAIZ}\n"
            f"e Alterar Ícone = {ICONE}")


def abrir_app() -> None:
    extra = {"creationflags": CREATE_NO_WINDOW} if WINDOWS else {}
    subprocess.Popen([str(PY_VENV), str(APP)], cwd=str(RAIZ), close_fds=True, **extra)


# ---------------------------------------------------------------- instalação
def instalar() -> Path | None:
    if sys.version_info < (3, 10):
        raise RuntimeError(f"precisa do Python 3.10 ou mais novo (este é o {sys.version.split()[0]}). "
                           "Baixe em python.org e marque 'Add python.exe to PATH'.")
    passo("1/5  Criando o ambiente virtual (venv)")
    if not PY_VENV.exists():
        rodar(sys.executable, "-m", "venv", VENV)
    else:
        print("venv já existe — reaproveitando.")
    passo("2/5  Instalando as bibliotecas (pode levar alguns minutos)")
    rodar(PY_VENV, "-m", "pip", "install", "--upgrade", "pip")
    rodar(PY_VENV, "-m", "pip", "install", "-r", "requirements_interface.txt")
    passo("3/5  Instalando a biblioteca da IQ Option")
    rodar(PY_VENV, "-m", "pip", "install", "-U", IQOPTIONAPI)
    rodar(PY_VENV, "-m", "pip", "install", "websocket-client==0.56.0")
    passo("4/5  Preparando a configuração")
    for modelo, final in ((".env.exemplo", ".env"), ("config.exemplo.yaml", "config.yaml")):
        if not (RAIZ / final).exists():
            shutil.copy(RAIZ / modelo, RAIZ / final)
            print(f"criado {final} (a partir de {modelo})")
        else:
            print(f"{final} já existe — mantido.")
    passo("5/5  Criando o atalho na Área de Trabalho")
    lnk = criar_atalho()
    if lnk:
        print(f"atalho criado: {lnk}")
    elif WINDOWS:
        print("Não consegui criar o atalho sozinho.\n" + instrucoes_atalho_manual())
    else:
        print("(atalho só no Windows) Para abrir: venv/bin/python app.py")
    return lnk


def main() -> int:
    print("IQBOT Quant Lab — instalação")
    interativo = bool(sys.stdin and sys.stdin.isatty())
    try:
        lnk = instalar()
    except Exception as e:  # noqa: BLE001
        print(f"\nERRO: {e}")
        print("Confira a mensagem acima. Sem internet ou com antivírus bloqueando, a instalação falha.")
        if interativo:
            input("\nAperte Enter para fechar esta janela...")
        return 1
    print("\nPRONTO!")
    if lnk:
        print('- Para abrir o app: dois cliques no ícone "IQBOT Quant Lab" da Área de Trabalho.')
    print(f'- Pelo CMD (nesta pasta): "{PY_VENV}" app.py')
    print("- Para testar sem conta: no app, escolha 'Simulação'.")
    print("- Para usar a conta Demo: abra o arquivo .env no Bloco de Notas e coloque seu e-mail e senha.")
    if interativo:
        resp = input("\nAbrir o IQBOT agora? [S/n] ").strip().lower()
        if resp in ("", "s", "sim", "y"):
            abrir_app()
    return 0


if __name__ == "__main__":
    sys.exit(main())
