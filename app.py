"""IQBOT Quant Lab — interface local do bot.

    venv\\Scripts\\python.exe app.py            abre a janela
    venv\\Scripts\\python.exe app.py --browser  abre no navegador
    venv\\Scripts\\python.exe app.py --oculto   reabre sem a janela do CMD (usado pelo atalho)

A interface só LIGA e DESLIGA o motor original (main.Bot). Estratégia, risco,
stop, notícias e executor continuam exatamente os mesmos do main.py.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True   # não cria as pastas __pycache__ (arquivos temporários do Python)


def relancar_sem_console(argv: list[str], plataforma: str = sys.platform) -> list[str] | None:
    """Com --oculto (usado pelo atalho), devolve o comando para reabrir o app sem janela de CMD.

    O atalho aponta para o python.exe do venv, que o Smart App Control do Windows 11 aceita
    (o pythonw.exe pode ser bloqueado). Esta cópia só reabre o app escondido e fecha.
    """
    if plataforma != "win32" or "--oculto" not in argv:
        return None
    from pathlib import Path as _Path
    return [sys.executable, str(_Path(__file__).resolve()), *[a for a in argv[1:] if a != "--oculto"]]


if __name__ == "__main__" and (_cmd := relancar_sem_console(sys.argv)):
    import subprocess
    from pathlib import Path as _Path
    subprocess.Popen(_cmd, cwd=str(_Path(__file__).resolve().parent),
                     creationflags=0x08000000, close_fds=True)          # CREATE_NO_WINDOW
    sys.exit(0)

import copy
from contextlib import closing
import json
import logging
import math
import secrets
import sqlite3
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

import configuracao
from database import BancoDados
from fake_broker import CorretoraFalsa, RelogioFalso, RelogioReal
from main import Bot, carregar_estrategia, configurar_log, desativar_edicao_rapida
from news import FiltroNoticias
from notifier import Notificador
from risk import FUSO_BR
from trava import MENSAGEM, Trava

ROOT = Path(__file__).parent
TOKEN = secrets.token_urlsafe(32)
log = logging.getLogger("bot.ui")


def clean(x):
    """Deixa o objeto pronto para JSON (sem NaN, datas em texto)."""
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, datetime):
        return x.isoformat()
    if hasattr(x, "item"):
        return clean(x.item())
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def velas_json(df: pd.DataFrame, n: int = 90) -> list[dict]:
    if df is None or not len(df):
        return []
    df = df.tail(n)
    return [{"t": i.isoformat() if hasattr(i, "isoformat") else str(i),
             "o": float(r["open"]), "h": float(r["high"]), "l": float(r["low"]), "c": float(r["close"])}
            for i, r in df.iterrows()]


def pasta_dados(mode: str) -> Path:
    """A simulação grava em pasta separada (não mistura humor e estado falsos com os reais)."""
    return ROOT / "data" / ("simulacao" if mode == "SIMULACAO" else "")


def nome_par(a: str) -> str:
    base = a.replace("-op", "").replace("-OTC", "")
    return f"{base[:3]}/{base[3:]}" + (" OTC" if a.endswith("-OTC") else "")


class Engine:
    def __init__(self):
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.bot = None
        self.corr = None
        self.db = None
        self.phase = "parado"          # parado | conectando | rodando | parando | erro
        self.error = ""
        self.mode = "SIMULACAO"
        self.logs: list[dict] = []
        self.cfg = configuracao.carregar()
        self.lista = list(self.cfg["ativos"]["lista"])
        self.selected = self.lista[0] if self.lista else ""
        self.trava = Trava(ROOT / "data" / "bot.lock")
        self._velas_cache: dict[str, list] = {}
        self.precisa_2fa = False
        self._codigo_2fa = ""
        self._ev_2fa = threading.Event()

    # ------------------------------------------------------------ automação
    def _automacao(self):
        """A automação do bot rodando, ou uma 'de leitura' quando o bot está parado."""
        if self.bot is not None and getattr(self.bot, "auto", None) is not None:
            return self.bot.auto
        if not (self.cfg.get("automacao") or {}).get("ativo"):
            return None
        chave = (self.mode, id(self.cfg))
        if getattr(self, "_auto_ocioso_chave", None) != chave:
            from seletor import Automacao
            self._auto_ocioso = Automacao(self.cfg, pasta_dados(self.mode), None)
            self._auto_ocioso_chave = chave
        return self._auto_ocioso

    def _trocas(self) -> list[dict]:
        p = ROOT / "data" / ("interface_simulacao.db" if self.mode == "SIMULACAO" else "bot.db")
        if not p.exists():
            return []
        try:
            with closing(sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)) as c:   # fecha o arquivo de verdade
                df = pd.read_sql_query("SELECT * FROM trocas_estrategia ORDER BY id DESC LIMIT 20", c)
        except Exception:  # noqa: BLE001
            return []
        import catalogo
        nome = lambda ch: catalogo.POR_CHAVE[ch].nome if ch in catalogo.POR_CHAVE else ch  # noqa: E731
        out = []
        for _, r in df.iterrows():
            t = pd.to_datetime(r["em"], utc=True, errors="coerce")
            out.append({"em": t.tz_convert(FUSO_BR).strftime("%d/%m %H:%M") if pd.notna(t) else "",
                        "mercado": "Mercado" if r["mercado"] == "op" else "OTC", "de": nome(r["de"]),
                        "para": nome(r["para"]), "quem": r["quem"], "motivo": r["motivo"]})
        return out

    def aplicar(self, d: dict):
        a = self._automacao()
        if a is None:
            raise ValueError("A automação está desligada no config.yaml (automacao.ativo).")
        if self.mode == "REAL" and str(d.get("confirmation", "")).strip() != self.cfg["conta"]["frase_confirmacao_real"]:
            raise ValueError("Na conta Real, digite a palavra de confirmação para trocar a estratégia.")
        a.aplicar(str(d.get("mercado")), str(d.get("chave")), quem="você")
        rodando = self.thread is not None and self.thread.is_alive()
        if not rodando:
            # bot parado: não há operação aberta, então a troca vale na hora
            arq = ROOT / "data" / ("interface_simulacao.db" if self.mode == "SIMULACAO" else "bot.db")
            a.db = BancoDados(arq)
            try:
                a.efetivar(True, datetime.now(timezone.utc))
            finally:
                a.db = None
            msg = "Estratégia trocada. Ela vale quando você iniciar o bot."
        else:
            msg = ("Troca pedida: entra no próximo minuto (na hora, se esse mercado não estiver operando; "
                   "se estiver, assim que não houver operação aberta).")
        self.note(msg)
        return msg

    def _config_tela(self) -> dict:
        """Valores atuais do config.yaml (o que está no arquivo, não o que o bot está usando)."""
        try:
            p = ROOT / "config.yaml"
            mt = p.stat().st_mtime if p.exists() else None
            if getattr(self, "_cfg_tela_mtime", -1) != mt:
                self._cfg_tela = configuracao.valores_tela(configuracao.carregar(p))
                self._cfg_tela_mtime = mt
            return self._cfg_tela
        except Exception:  # noqa: BLE001
            return configuracao.valores_tela(self.cfg)

    def salvar_config(self, d: dict) -> str:
        """Grava no config.yaml as regras editadas na tela. Pede a palavra de confirmação."""
        if str(d.get("confirmation", "")).strip() != self.cfg["conta"]["frase_confirmacao_real"]:
            raise ValueError("Digite a palavra de confirmação para salvar as regras.")
        valores = d.get("valores") or {}
        try:
            cfg = configuracao.salvar_campos(valores, ROOT / "config.yaml")
        except configuracao.ErroConfig as e:
            raise ValueError(str(e)) from None
        rodando = self.thread is not None and self.thread.is_alive()
        if not rodando:
            with self.lock:
                self.cfg = cfg
            msg = "Regras salvas. Elas valem quando você clicar em Iniciar."
        else:
            msg = "Regras salvas. O bot está rodando com as anteriores: elas valem quando você parar e iniciar de novo."
        self.note(msg)
        return msg

    def ignorar(self, d: dict):
        a = self._automacao()
        if a is not None:
            a.ignorar(str(d.get("id")))

    def reavaliar(self):
        a = self._automacao()
        if a is None:
            raise ValueError("A automação está desligada no config.yaml (automacao.ativo).")
        rodando = self.bot is not None and self.phase == "rodando"
        if not a.reavaliar(self.corr if rodando else None, baixar=rodando):
            raise ValueError("Já existe uma avaliação em andamento.")
        self.note("Reavaliação iniciada" + ("" if rodando else " (com as velas já baixadas — bot parado)") + ".")

    # ------------------------------------------------------------ 2FA pela janela (sem CMD)
    def pedir_2fa(self, _prompt: str = "") -> str:
        """Chamado pela conexão quando a IQ pede o código 2FA: espera a pessoa digitar na tela."""
        with self.lock:
            self.precisa_2fa = True
            self._codigo_2fa = ""
            self._ev_2fa.clear()
        self.note("A IQ pediu o código de verificação (2FA). Digite na janela.")
        ok = self._ev_2fa.wait(300)
        with self.lock:
            self.precisa_2fa = False
        if not ok or not self._codigo_2fa:
            raise RuntimeError("Código 2FA não informado em 5 minutos.")
        return self._codigo_2fa

    def informar_2fa(self, codigo: str):
        codigo = str(codigo or "").strip()
        if not (codigo.isdigit() and 4 <= len(codigo) <= 8):
            raise ValueError("O código deve ter só números (4 a 8 dígitos).")
        with self.lock:
            if not self.precisa_2fa:
                raise ValueError("Nenhum código foi pedido agora.")
            self._codigo_2fa = codigo
        self._ev_2fa.set()

    # ------------------------------------------------------------ log
    def note(self, msg: str):
        with self.lock:
            self.logs.append({"t": datetime.now().strftime("%H:%M:%S"), "m": msg})
            self.logs = self.logs[-200:]

    # ------------------------------------------------------------ leitura do banco
    def _db_leitura(self):
        if self.db is not None:
            return self.db.operacoes_df(self._conta())
        p = ROOT / "data" / ("interface_simulacao.db" if self.mode == "SIMULACAO" else "bot.db")
        if not p.exists():
            return pd.DataFrame()
        with closing(sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)) as c:   # fecha o arquivo de verdade
            df = pd.read_sql_query("SELECT * FROM operacoes ORDER BY id", c)
        conta = self._conta()
        return df[df["conta"] == conta] if "conta" in df else df

    def _conta(self) -> str:
        return "SIMULACAO" if self.mode == "SIMULACAO" else self.mode

    def _hoje(self, df: pd.DataFrame) -> dict:
        vazio = {"ops": 0, "wins": 0, "losses": 0, "acerto": None, "lucro": 0.0}
        if df is None or not len(df):
            return vazio
        t = pd.to_datetime(df["aberta_em"], utc=True, errors="coerce").dt.tz_convert(FUSO_BR)
        hoje = datetime.now(timezone.utc).astimezone(FUSO_BR).date()
        if self.bot is not None:
            hoje = self.bot.rel.agora_dt().astimezone(FUSO_BR).date()
        d = df[(t.dt.date == hoje) & df["resultado"].isin(["win", "loose", "equal"])]
        w, l = int((d["resultado"] == "win").sum()), int((d["resultado"] == "loose").sum())
        return {"ops": int(len(d)), "wins": w, "losses": l,
                "acerto": (w / (w + l)) if (w + l) else None, "lucro": float(d["lucro"].fillna(0).sum())}

    def _ultimas(self, df: pd.DataFrame, n: int = 15) -> list[dict]:
        if df is None or not len(df):
            return []
        out = []
        hoje = (self.bot.rel.agora_dt() if self.bot is not None else datetime.now(timezone.utc)).astimezone(FUSO_BR).date()
        for _, o in df.tail(n).iloc[::-1].iterrows():
            t = pd.to_datetime(o["aberta_em"], utc=True, errors="coerce")
            tb = t.tz_convert(FUSO_BR) if pd.notna(t) else None
            out.append({"hora": "" if tb is None else tb.strftime("%H:%M" if tb.date() == hoje else "%d/%m %H:%M"),
                        "ativo": nome_par(str(o["ativo"])), "lado": str(o["direcao"] or "").upper(),
                        "resultado": {"win": "ganhou", "loose": "perdeu", "equal": "empate",
                                      "erro": "recusada"}.get(o["resultado"], "aberta"),
                        "lucro": None if pd.isna(o["lucro"]) else float(o["lucro"])})
        return out

    # ------------------------------------------------------------ status em linguagem simples
    def _status(self) -> dict:
        if self.phase == "erro":
            return {"tipo": "erro", "titulo": "Erro", "texto": self.error or "O motor parou com erro."}
        if self.precisa_2fa:
            return {"tipo": "aguardando", "titulo": "Código de verificação",
                    "texto": "A IQ enviou um código por SMS ou e-mail. Digite na janelinha que abriu."}
        if self.phase == "conectando":
            return {"tipo": "aguardando", "titulo": "Conectando…", "texto": "Entrando na IQ Option. Se pedir código 2FA, veja a janela do CMD."}
        if self.phase == "parando":
            return {"tipo": "aguardando", "titulo": "Parando…", "texto": "Não abre novas operações. Esperando as abertas terminarem."}
        if self.phase != "rodando" or self.bot is None:
            return {"tipo": "parado", "titulo": "Bot parado", "texto": "Escolha a conta e clique em Iniciar."}
        b = self.bot
        s = str(b.status or "")
        abertos = getattr(b, "abertos", {}) or {}
        agora = b.rel.agora_dt().astimezone(FUSO_BR)
        if s.startswith("PAUSADO — STOP LOSS"):
            return {"tipo": "stop", "titulo": "Parado pelo stop loss",
                    "texto": "Perdeu 2 seguidas, então não opera mais. Para recomeçar: Parar e depois Iniciar."}
        if s.startswith("PAUSADO — stop diário"):
            return {"tipo": "stop", "titulo": "Stop diário atingido", "texto": "Perdeu o limite do dia. Volta a operar amanhã."}
        if s.startswith("PAUSADO — aguardando resultado"):
            return {"tipo": "aguardando", "titulo": "Esperando resultado",
                    "texto": "Falta 1 perda para o stop: só entra de novo depois que a operação aberta fechar."}
        if s.startswith("PAUSADO"):
            return {"tipo": "pausado", "titulo": "Pausado", "texto": s.split("—", 1)[-1].strip()}
        if s.startswith("SINAL BLOQUEADO"):
            return {"tipo": "pausado", "titulo": "Sinal bloqueado", "texto": s.split("—", 1)[-1].strip()}
        if s.startswith("AGUARDANDO"):
            return {"tipo": "aguardando", "titulo": "Aguardando", "texto": s.split("—", 1)[-1].strip()}
        tem_op = any(a.endswith("-op") for a in abertos)
        if b.auto is None and not tem_op and agora.weekday() < 5 and 9 <= agora.hour < 18:
            return {"tipo": "aguardando", "titulo": "Aguardando os pares abrirem",
                    "texto": "A IQ está com os pares de mercado fechados nas binárias. O humor só grava até as 18h."}
        if s.startswith("SEM ATIVOS"):
            return {"tipo": "aguardando", "titulo": "Nenhum par disponível", "texto": s.split("—", 1)[-1].strip()}
        extra = s.split("—", 1)[-1].strip() if s.startswith("operando —") else ""
        n_abertos = sum(1 for a in self.lista if a in abertos)
        if b.auto is not None:
            ativos_pl = [pl for pl in b.planos_atuais if pl.pode]
            n_abertos = sum(len(pl.pares) for pl in ativos_pl)
            if ativos_pl and not extra:
                extra = " · ".join(f"{pl.estrategia.nome}{' (em teste)' if pl.status != 'aprovada' else ''} "
                                   f"em {len(pl.pares)} par(es) {'de mercado' if pl.mercado == 'op' else 'OTC'}"
                                   for pl in ativos_pl) + ". Entra sozinho quando aparecer sinal."
        return {"tipo": "operando", "titulo": "Operando",
                "texto": extra or f"Procurando sinais em {n_abertos} par(es) aberto(s). Entra sozinho quando aparecer."}

    def _lista_tela(self) -> list[str]:
        b = self.bot
        if b is not None and getattr(b, "auto", None) is not None and b.planos_atuais:
            pares = []
            for pl in b.planos_atuais:
                pares += sorted(pl.pares, key=lambda x: -pl.pares[x])
            if pares:
                return pares[:12]
        return self.lista

    def _pares(self) -> list[dict]:
        b = self.bot
        abertos = (getattr(b, "abertos", {}) or {}) if b else {}
        sinais = b.sinais if b else {}
        out = []
        for a in self._lista_tela():
            out.append({"id": a, "nome": nome_par(a), "aberto": (a in abertos) if b else None,
                        "payout": abertos.get(a), "sinal": sinais.get(a, "")})
        return out

    def _velas(self) -> list[dict]:
        a = self.selected
        if self.bot is not None:
            df = self.bot.cache.dfs.get(a)
            if df is not None and len(df):
                return velas_json(df)
            return self._velas_cache.get(a, [])
        if a not in self._velas_cache:
            p = ROOT / "data" / "iq" / f"{a}.csv.gz"
            try:
                self._velas_cache[a] = velas_json(pd.read_csv(p, index_col=0, parse_dates=True)) if p.exists() else []
            except Exception:  # noqa: BLE001
                self._velas_cache[a] = []
        return self._velas_cache[a]

    # ------------------------------------------------------------ estado para a tela
    def state(self) -> dict:
        with self.lock:
            df = self._db_leitura()
            b = self.bot
            r = self.cfg["risco"]
            perdas = 0
            if b is not None:
                perdas = max(b.risco.perdas_seguidas, b.risco._perdas_finais())
            e = self.cfg["estrategia"]
            rev = e.get("reversao_op") or {}
            auto = self._automacao()
            auto_estado = None
            if auto is not None:
                agora = b.rel.agora_dt() if b is not None else datetime.now(timezone.utc)
                auto_estado = auto.estado(agora)
                import avaliador
                auto_estado["ao_vivo"] = avaliador.desempenho_ao_vivo(df)
                auto_estado["trocas"] = self._trocas()
                rot = {"aprovada": "aprovada", "em_teste": "em teste", "reprovada": "não aprovada",
                       "sem_avaliacao": "ainda não avaliada"}
                pl = auto_estado["planos"]
                estr = (f"Mercado: {pl['op']['nome']} ({rot[pl['op']['status']]}) · "
                        f"OTC: {pl['otc']['nome']} ({rot[pl['otc']['status']]}). Modo {auto_estado['modo']}.")
            elif e["manual"]["familia"] == "humor" and rev.get("ativo"):
                h = rev.get("horas_brt", [9, 18])
                estr = f"Reversão 5 min nos pares de mercado ({h[0]}h–{h[1]}h) · Humor no OTC (fora desse horário)"
            else:
                estr = f"{e['manual']['familia']} {e['manual']['params']}"
            return clean({
                "phase": self.phase, "mode": self.mode, "status": self._status(), "precisa_2fa": self.precisa_2fa,
                "conta": {"saldo": b.saldo if b else None, "moeda": b.moeda if b else "",
                          "orcamento": b.risco.orcamento_restante() if b else None},
                "hoje": self._hoje(df),
                "perdas": {"seguidas": perdas, "max": r["max_perdas_seguidas"]},
                "risco": {"entrada": r["entrada"], "max_perdas": r["max_perdas_seguidas"],
                          "stop_dia": r["stop_diario_pct"], "orcamento": r["orcamento_teste"],
                          "sessao": float(r.get("perda_max_sessao") or 0),
                          "dia_valor": float(r.get("stop_diario_valor") or 0),
                          "simult": self.cfg["ativos"]["max_ativos_simultaneos"],
                          "martingale": r["martingale"]["ativo"]},
                "estrategia": estr, "auto": auto_estado,
                "frase": self.cfg["conta"]["frase_confirmacao_real"],
                "config": self._config_tela(),
                "pares": self._pares(), "selected": self.selected, "selected_nome": nome_par(self.selected) if self.selected else "",
                "velas": self._velas(), "ops": self._ultimas(df), "logs": self.logs[-80:],
            })

    # ------------------------------------------------------------ controle
    def start(self, data: dict):
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError("O bot já está rodando (ou terminando de parar).")
            mode = data.get("mode", "SIMULACAO")
            if mode not in ("SIMULACAO", "PRACTICE", "REAL"):
                raise ValueError("Conta inválida.")
            if mode == "REAL" and str(data.get("confirmation", "")).strip() != self.cfg["conta"]["frase_confirmacao_real"]:
                raise ValueError("Palavra de confirmação incorreta.")
            if (ROOT / "STOP").exists():
                raise ValueError("Existe o arquivo STOP na pasta do bot. Apague-o para poder iniciar.")
            if mode != "SIMULACAO" and not self.trava.adquirir():
                raise ValueError(MENSAGEM)
            self.cfg = configuracao.carregar()   # relê o config.yaml a cada início
            self.lista = list(self.cfg["ativos"]["lista"])
            if self.selected not in self.lista and self.lista:
                self.selected = self.lista[0]
            self.mode = mode
            self.phase = "conectando"
            self.error = ""
            self.stop.clear()
            self.bot = None
            self.db = None
            self._velas_cache = {}
            self.thread = threading.Thread(target=self.run, daemon=False)
            self.thread.start()

    def trocar_modo(self, mode: str):
        """Com o bot parado, troca a conta exibida (histórico e resultado de hoje)."""
        with self.lock:
            if mode not in ("SIMULACAO", "PRACTICE", "REAL"):
                raise ValueError("Conta inválida.")
            if self.thread and self.thread.is_alive():
                return
            if mode != self.mode:
                self.mode, self.bot, self.db, self.corr = mode, None, None, None

    def _ao_vivo(self, bot, corr):
        """Deixa a tela ao vivo enquanto o bot roda: saldo a cada 30 s, pares abertos e payouts a cada 60 s.
        (Antes o saldo só era lido de 10 em 10 minutos e os payouts de 3 em 3.)"""
        from mercado import separar
        ultimo_pares = time.monotonic()
        while not self.stop.wait(30):        # a cada 30 s (menos chamadas simultâneas à IQ)
            if self.bot is not bot:
                return
            try:
                bot.saldo = float(corr.saldo())
            except Exception as e:  # noqa: BLE001
                log.debug("saldo ao vivo falhou: %s", e)
            if getattr(bot, "auto", None) is not None and time.monotonic() - ultimo_pares >= 60:
                ultimo_pares = time.monotonic()
                try:
                    op, otc = separar(corr.ativos_abertos())
                    bot.abertos = {**op, **otc}
                except Exception as e:  # noqa: BLE001
                    log.debug("pares ao vivo falharam: %s", e)

    def halt(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                self.phase = "parando"
                self.stop.set()
                self.note("Parando: sem novas entradas; esperando as operações abertas terminarem.")

    def run(self):
        vigia = None
        try:
            cfg = copy.deepcopy(self.cfg)
            cfg["telegram"]["ativo"] = False
            cfg["painel"]["ativo"] = False
            estrat = carregar_estrategia(cfg)
            if self.mode == "SIMULACAO":
                rel = RelogioFalso(time.time())
                ativos_sim = {"EURGBP-op": 0.86, "GBPUSD-op": 1.27, "AUDUSD-op": 0.66,
                              "EURUSD-OTC": 1.10, "AUDCAD-OTC": 0.90, "GBPJPY-OTC": 190.0}
                for a in self.lista:
                    ativos_sim.setdefault(a, 1.0)
                corr = CorretoraFalsa(rel, ativos=ativos_sim)
                cfg["noticias"]["ativo"] = False
                if cfg.get("automacao"):
                    cfg["automacao"]["reavaliar_horas"] = 0   # simulação não reavalia (velas falsas)
                arq_sim = ROOT / "data" / "interface_simulacao.db"
                db = BancoDados(arq_sim)
                with db._con() as c:              # cada simulação começa do zero
                    for tabela in ("operacoes", "sinais_bloqueados", "eventos", "trocas_estrategia"):
                        c.execute(f"DELETE FROM {tabela}")
                conta = "SIMULACAO"
            else:
                from dotenv import load_dotenv
                load_dotenv(ROOT / ".env")
                from connection import CorretoraIQ
                corr = CorretoraIQ(timeout=cfg["conexao"]["timeout_s"], max_tentativas=cfg["conexao"]["max_tentativas"],
                                   pedir_2fa=self.pedir_2fa)
                self.note(f"Conectando na conta {self.mode}…")
                corr.conectar(self.mode)
                rel = RelogioReal(corr.hora_servidor() - time.time())
                db = BancoDados(ROOT / "data" / "bot.db")
                conta = self.mode
            noticias = FiltroNoticias(cfg["noticias"], ROOT / "data" / "noticias")
            pd_ = pasta_dados(self.mode)
            pd_.mkdir(parents=True, exist_ok=True)
            if self.mode == "SIMULACAO" and (ROOT / "data" / "avaliacao.json").exists():
                import shutil   # a simulação usa uma CÓPIA da avaliação real (só para mostrar)
                shutil.copy2(ROOT / "data" / "avaliacao.json", pd_ / "avaliacao.json")
            bot = Bot(cfg, corr, rel, db, conta, estrat, noticias, Notificador(False), ROOT, pasta_dados=pd_)
            with self.lock:
                self.bot, self.corr, self.db = bot, corr, db
                self.phase = "parando" if self.stop.is_set() else "rodando"
            self.note(f"Bot iniciado na conta {self.mode}.")
            if self.mode != "SIMULACAO":
                threading.Thread(target=self._ao_vivo, args=(bot, corr), daemon=True).start()
            atraso = cfg["estrategia"]["atraso_entrada_s"]
            # Vigia: se um ciclo passar de 3 min, grava onde travou em logs/travamento.log
            if self.mode != "SIMULACAO":
                import faulthandler
                (ROOT / "logs").mkdir(exist_ok=True)
                vigia = open(ROOT / "logs" / "travamento.log", "a", encoding="utf-8")
            while not self.stop.is_set():
                if vigia:
                    faulthandler.dump_traceback_later(180, repeat=True, file=vigia)
                if self.mode == "SIMULACAO":
                    agora = rel.agora()
                    rel.dormir((agora // 60 + 1) * 60 + atraso - agora)
                else:
                    espera = (rel.agora() // 60 + 1) * 60 + atraso - rel.agora()
                    if self.stop.wait(max(0.0, espera)):
                        break
                if self.stop.is_set():
                    break
                try:
                    bot.passo()
                except Exception as e:  # noqa: BLE001  (um erro pontual não derruba o bot)
                    log.exception("erro no passo: %s", e)
                    bot.status = f"erro: {e}"
                if self.selected not in bot.cache.dfs:   # velas só para o gráfico
                    try:
                        self._velas_cache[self.selected] = velas_json(corr.velas(self.selected, 60, 90))
                    except Exception:  # noqa: BLE001
                        pass
                if self.mode == "SIMULACAO" and self.stop.wait(2):
                    break
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
            self.note("Erro: " + str(e))
            log.exception("erro na interface: %s", e)
        finally:
            try:
                import faulthandler
                faulthandler.cancel_dump_traceback_later()
                if vigia:
                    vigia.close()
            except Exception:  # noqa: BLE001
                pass
            if self.bot:
                with self.lock:
                    self.phase = "parando"
                self.bot.exec.aguardar_todas(400)
            if self.mode != "SIMULACAO":
                self.trava.liberar()
            with self.lock:
                self.phase = "erro" if self.error else "parado"
            self.note("Bot parado.")


class LogHandler(logging.Handler):
    def emit(self, record):
        if record.name.startswith("bot") and engine is not None:
            engine.note(record.getMessage())


engine: Engine | None = None
ARQUIVOS = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css",
            "/assets/anjo.jpg": "assets/anjo.jpg",
            "/assets/anjo-icon.png": "assets/anjo-icon.png",
            "/assets/anjo.ico": "assets/anjo.ico",
            "/assets/fonts/inter.woff2": "assets/fonts/inter.woff2",
            "/assets/fonts/jetbrains-mono.woff2": "assets/fonts/jetbrains-mono.woff2"}
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg",
        ".ico": "image/x-icon", ".woff2": "font/woff2", ".webm": "video/webm"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, obj, status=200):
        b = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/state":
            if self.headers.get("X-Token") != TOKEN:
                return self.send({"error": "Não autorizado"}, 403)
            try:
                return self.send(engine.state())
            except Exception as e:  # noqa: BLE001
                return self.send({"error": str(e)}, 500)
        if path not in ARQUIVOS:
            return self.send({"error": "Não encontrado"}, 404)
        p = ROOT / "interface" / ARQUIVOS[path]
        if not p.exists():
            return self.send({"error": "Não encontrado"}, 404)
        b = p.read_bytes()
        if path == "/":
            b = b.replace(b"__TOKEN__", TOKEN.encode())
        total = len(b)
        faixa = self.headers.get("Range", "")
        if faixa.startswith("bytes=") and p.suffix == ".webm":   # vídeo: o navegador pede pedaços
            ini, _, fim = faixa[6:].split(",")[0].partition("-")
            ini = int(ini) if ini else 0
            fim = min(int(fim), total - 1) if fim else total - 1
            if ini >= total or ini > fim:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{total}")
                self.end_headers()
                return
            b = b[ini:fim + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {ini}-{fim}/{total}")
        else:
            self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", MIME[p.suffix])
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        if self.headers.get("X-Token") != TOKEN:
            return self.send({"error": "Não autorizado"}, 403)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size > 4096:
                raise ValueError("Requisição inválida")
            d = json.loads(self.rfile.read(size) or "{}")
            if self.path == "/api/start":
                engine.start(d)
            elif self.path == "/api/stop":
                engine.halt()
            elif self.path == "/api/config":
                return self.send({"ok": True, "msg": engine.salvar_config(d)})
            elif self.path == "/api/aplicar":
                return self.send({"ok": True, "msg": engine.aplicar(d)})
            elif self.path == "/api/ignorar":
                engine.ignorar(d)
            elif self.path == "/api/reavaliar":
                engine.reavaliar()
            elif self.path == "/api/2fa":
                engine.informar_2fa(d.get("codigo", ""))
            elif self.path == "/api/modo":
                engine.trocar_modo(d.get("mode", ""))
            elif self.path == "/api/select":
                if d.get("asset") not in engine.lista and d.get("asset") not in engine._lista_tela():
                    raise ValueError("Par inválido")
                engine.selected = d["asset"]
            else:
                return self.send({"error": "Não encontrado"}, 404)
            self.send({"ok": True})
        except (ValueError, TypeError) as e:
            self.send({"error": str(e)}, 400)


def style_native_window(window):
    """Barra de título escura e ícone no Windows (mantém minimizar/maximizar/fechar)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes
        hwnd = wintypes.HWND(window.native.Handle.ToInt64())
        dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
        dwm.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.restype = ctypes.c_long
        for attribute, value in [(20, 1), (34, 0x00160e08), (35, 0x001c120a), (36, 0x00efe5d8)]:
            color = ctypes.c_uint32(value)
            dwm(hwnd, attribute, ctypes.byref(color), ctypes.sizeof(color))
        user32 = ctypes.windll.user32
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                      ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
        user32.SendMessageW.restype = ctypes.c_ssize_t
        handles = []
        for kind, size in [(0, 16), (1, 32)]:
            icon = user32.LoadImageW(None, str(ICONE), 1, size, size, 0x0010)
            if icon:
                handles.append(icon)
                user32.SendMessageW(hwnd, 0x0080, kind, icon)
        window._quantlab_icons = handles
    except Exception as exc:  # noqa: BLE001
        log.warning("Tema da janela indisponível: %s", exc)


ICONE = ROOT / "interface" / "assets" / "anjo.ico"


def identidade_windows():
    """Faz o Windows tratar o app como programa próprio (não como 'Python'):
    assim a barra de tarefas usa o ícone da janela e não agrupa com outros Pythons."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("IQBOT.QuantLab")
    except Exception as exc:  # noqa: BLE001
        log.warning("AppUserModelID indisponível: %s", exc)


def limpar_simulacao(raiz: Path = ROOT) -> None:
    """Ao fechar o app, apaga o que a conta Simulação criou (se recria na próxima simulação)."""
    import shutil
    try:
        (raiz / "data" / "interface_simulacao.db").unlink(missing_ok=True)
    except OSError as e:
        log.debug("não apaguei o banco da simulação: %s", e)
    shutil.rmtree(raiz / "data" / "simulacao", ignore_errors=True)


def main():
    global engine
    identidade_windows()               # antes de criar qualquer janela
    configurar_log(ROOT / "logs")      # continua gravando logs/bot.log (para análise)
    desativar_edicao_rapida()          # clique no CMD não congela o programa
    engine = Engine()
    logging.getLogger().addHandler(LogHandler())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    print("IQBOT Quant Lab: " + url, flush=True)
    try:
        if "--browser" not in sys.argv:
            try:
                import webview
            except ImportError:
                webview = None
            if webview:
                win = webview.create_window("IQBOT · Quant Lab", url, width=1440, height=900,
                                            min_size=(1100, 720), background_color="#080e16")

                def ao_fechar():
                    if engine.thread and engine.thread.is_alive():
                        engine.halt()
                        return False
                win.events.closing += ao_fechar
                win.events.before_show += style_native_window
                try:   # pywebview 5+: ícone da janela e da barra de tarefas
                    webview.start(icon=str(ICONE))
                except TypeError:
                    webview.start()
                return
        webbrowser.open(url)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        engine.halt()
        if engine.thread:
            engine.thread.join()
        server.shutdown()
        limpar_simulacao()


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001  (sem CMD aberto, o erro precisa aparecer em algum lugar)
        import traceback
        (ROOT / "logs").mkdir(exist_ok=True)
        (ROOT / "logs" / "interface_erro.log").write_text(traceback.format_exc(), encoding="utf-8")
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "A interface não abriu. Detalhes em logs\\interface_erro.log",
                                             "IQBOT Quant Lab", 0x10)
        raise
