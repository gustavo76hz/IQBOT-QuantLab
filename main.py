"""Bot de opções binárias (turbo) para IQ Option — com validação estatística.

    python main.py                 opera conforme config.yaml (REAL pede frase de confirmação)
    python main.py --simular       corretora FALSA, relógio acelerado (testa tudo sem conta)
    python main.py --sem-painel    sem o painel colorido (só texto)
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True   # não cria as pastas __pycache__ (arquivos temporários do Python)

import argparse
import json
import logging
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pandas as pd

import asset_selector
import configuracao
import monitor as mon
import stats
import strategy as st
from data import CacheVelas
from database import BancoDados
from executor import Executor
from fake_broker import CorretoraFalsa, RelogioFalso, RelogioReal
from news import FiltroNoticias
from notifier import Notificador
from risk import FUSO_BR, GestorRisco, tabela_martingale

RAIZ = Path(__file__).parent
log = logging.getLogger("bot")
MOEDAS = "USD|EUR|GBP|JPY|AUD|NZD|CAD|CHF|SEK|NOK|SGD|HKD|MXN|TRY|ZAR|PLN|CNH|DKK|HUF|CZK|INR|BRL"
PAR_MOEDA_OTC = __import__("re").compile(rf"({MOEDAS})({MOEDAS})-OTC")  # só moedas (sem cripto/índices)
PARES_USD = ["EURUSD-op", "GBPUSD-op", "USDJPY-op", "AUDUSD-op", "USDCAD-op", "USDCHF-op"]


def configurar_log(pasta: Path):
    pasta.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(pasta / "bot.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    if not any(isinstance(x, RotatingFileHandler) for x in raiz.handlers):
        raiz.addHandler(h)


def carregar_estrategia(cfg: dict, raiz: Path = RAIZ) -> dict:
    e = cfg["estrategia"]
    arq = raiz / e["arquivo_congelada"]
    if e["fonte"] in ("auto", "congelada") and arq.exists():
        d = json.loads(arq.read_text(encoding="utf-8"))
        out = {"familia": d["familia"], "params": d["params"], "ativo": d.get("ativo", "TODOS"),
               "validada": True, "origem": str(arq)}
    elif e["fonte"] == "congelada":
        raise SystemExit("estrategia.fonte = congelada, mas não existe regra aprovada "
                         f"({arq}). Rode a pesquisa ou mude para 'manual'.")
    else:
        out = {"familia": e["manual"]["familia"], "params": dict(e["manual"]["params"]),
               "ativo": "TODOS", "validada": False, "origem": "config.yaml (manual)"}
    out["params"].setdefault("exp", 1)
    out["params"].setdefault("lado", 1)
    out["id"] = f"{out['familia']}:{json.dumps(out['params'], sort_keys=True)}"
    return out


def confirmar_real(cfg: dict, estrat: dict, pedir=input) -> bool:
    r = cfg["risco"]
    pay = 0.85
    be = stats.breakeven(pay)
    perda_media = -stats.expectativa(0.5, pay) * r["entrada"]
    print("=" * 72)
    print("  ATENÇÃO: CONTA REAL — DINHEIRO DE VERDADE")
    print("=" * 72)
    if not estrat["validada"]:
        print("  A estratégia em uso NÃO foi aprovada pela pesquisa. Não há evidência de")
        print("  vantagem. Com acerto de 50% e payout de 85% (breakeven {:.1%}),".format(be))
        print(f"  a perda MÉDIA é {perda_media:.2f} por operação de {r['entrada']:.2f}.")
        print(f"  Em 100 operações: cerca de {100 * perda_media:.2f} de prejuízo esperado.")
    print(f"  Teto de perda total (orçamento): {r['orcamento_teste']:.2f}  | stop diário {r['stop_diario_pct']}%")
    if r["martingale"]["ativo"]:
        print(tabela_martingale(r["entrada"], r["martingale"]["fator"], r["martingale"]["max_gales"], pay))
    print("  A IQ Option não é autorizada pela CVM no Brasil: não há proteção regulatória.")
    print("=" * 72)
    frase = cfg["conta"]["frase_confirmacao_real"]
    resp = pedir(f'Digite exatamente: {frase}\n> ').strip()
    return resp == frase


class Bot:
    def __init__(self, cfg: dict, corretora, relogio, banco: BancoDados, conta: str,
                 estrategia: dict, noticias: FiltroNoticias | None = None,
                 notificador: Notificador | None = None, pasta_stop: Path = RAIZ, painel: bool = False,
                 pasta_dados: Path | None = None):
        self.cfg = cfg
        self.c = corretora
        self.rel = relogio
        self.db = banco
        self.conta = conta
        self.est = estrategia
        self.noticias = noticias or FiltroNoticias(dict(cfg["noticias"], ativo=False))
        self.notif = notificador or Notificador(False)
        saldo = float(self.c.saldo())
        dia_br = self.rel.agora_dt().astimezone(FUSO_BR).date().isoformat()
        self.risco = GestorRisco(cfg["risco"], saldo, self.db.lucro_total(conta), pasta_stop,
                                 hoje=self.db.resumo_do_dia(conta, dia_br))
        self.exec = Executor(self.c, self.db, self.risco, self.notif, conta, relogio)
        self.cache = CacheVelas(self.c, 60, cfg["estrategia"]["candles_historico"])
        self.selecionados: list[tuple[str, float, dict]] = []
        self.ultima_selecao = -1e18
        self.saldo = saldo
        self.moeda = self.c.moeda()
        self.status = "iniciando"
        self.sinais: dict[str, str] = {}
        self.minutos = 0
        self.painel = painel
        # onde gravar humor e estado da automação (a simulação usa uma pasta SEPARADA,
        # para não misturar dados falsos com os reais)
        self.pasta_dados = Path(pasta_dados) if pasta_dados else Path(pasta_stop) / "data"
        # automação de pares e estratégias (seletor.py). Desligada = comportamento antigo.
        self.auto = None
        self.planos_atuais = []
        if (cfg.get("automacao") or {}).get("ativo"):
            from seletor import Automacao
            self.auto = Automacao(cfg, self.pasta_dados, self.db, self.c)

    # ------------------------------------------------------------------
    def _lista_permitida(self, abertos: dict[str, float]) -> dict[str, float]:
        lista = set(self.cfg["ativos"]["lista"])
        ok = {}
        for a, p in abertos.items():
            if a in lista or (self.cfg["ativos"]["incluir_otc"] and PAR_MOEDA_OTC.fullmatch(a)):
                base = a.split("-")[0].upper()
                alvo = str(self.est.get("ativo", "TODOS")).upper()
                if alvo in ("TODOS", base):
                    ok[a] = p
        return ok

    def selecionar_ativos(self):
        todos = self.c.ativos_abertos()
        abertos = self._lista_permitida(todos)
        self.abertos = abertos  # usado pela tática de humor (olha todos os pares abertos)
        # diagnóstico: por que cada ativo da lista entrou ou ficou de fora
        diag = []
        for a in self.cfg["ativos"]["lista"]:
            if a not in todos:
                diag.append(f"{a}=FECHADO")
            elif a in abertos:
                diag.append(f"{a}={abertos[a]:.0%}")
        log.info("lista do config: %s", " ".join(diag))
        for a in abertos:
            if self.est["familia"] != "humor" and a not in self.cache.dfs:  # humor não usa velas
                try:
                    self.cache.atualizar(a)
                except Exception as e:  # noqa: BLE001
                    log.warning("sem velas de %s: %s", a, e)
        self.selecionados = asset_selector.escolher(abertos, self.cache.dfs, self.cfg["ativos"],
                                                    self.est.get("ativo") if self.est["validada"] else None)
        self.ultima_selecao = self.rel.agora()
        log.info("ativos: %s", [(a, n) for a, n, _ in self.selecionados])
        if not self.selecionados:
            motivos = []
            for a, pay in abertos.items():
                n, _ = asset_selector.nota(a, pay, self.cache.dfs.get(a), None)
                if pay < self.cfg["ativos"]["payout_minimo"]:
                    motivos.append(f"{a}: payout {pay:.0%} < mínimo {self.cfg['ativos']['payout_minimo']:.0%}")
                else:
                    motivos.append(f"{a}: nota {n:.0f} < mínima {self.cfg['ativos']['nota_minima']}")
            self._motivo_vazio = "SEM ATIVOS — " + ("; ".join(motivos) or "nenhum par da lista está aberto agora")
            log.warning(self._motivo_vazio)

    def _usd_index(self, ativo: str):
        if not self.est["familia"] == "dxy":
            return None, 0
        base = ativo.split("-")[0].upper()
        closes = {}
        for p in PARES_USD:
            if p.split("-")[0] != base:
                try:
                    closes[p] = self.cache.atualizar(p)["close"]
                except Exception:  # noqa: BLE001
                    pass
        return st.indice_usd(closes), st.EXPOSICAO_USD.get(base, 0)

    def estado(self) -> dict:
        s = mon.avaliar(self.db.operacoes_df(self.conta, self.est["id"]),
                        min_operacoes=self.cfg["monitor"]["min_operacoes"])
        return {"conta": self.conta, "saldo": self.saldo, "moeda": self.moeda,
                "lucro_dia": self.risco.lucro_dia, "orcamento": self.risco.orcamento_restante(),
                "hora": self.rel.agora_dt().astimezone(FUSO_BR).strftime("%d/%m %H:%M:%S BRT"),
                "estrategia": f"{self.est['familia']} {self.est['params']}" + (
                    " + reversão -op {}h-{}h".format(*self.cfg["estrategia"]["reversao_op"].get("horas_brt", [9, 18]))
                    if self.est["familia"] == "humor" and (self.cfg["estrategia"].get("reversao_op") or {}).get("ativo")
                    else ""), "validada": self.est["validada"],
                "monitor": s.texto, "status": self.status,
                "ativos": [{"ativo": a, "nota": n, "payout": d["payout_valor"], "sinal": self.sinais.get(a, "-")}
                           for a, n, d in self.selecionados],
                "ops": self.exec.ultimas}

    # ------------------------------------------------------------------
    def passo(self):
        """Executado logo após a abertura de cada vela de 1 minuto."""
        agora_ts = self.rel.agora()
        agora = self.rel.agora_dt()
        self.minutos += 1
        self.ultimo_passo_real = time.time()      # a thread da reabertura só age com o bot vivo
        self._bloqueios = []  # sinais barrados neste minuto (para o painel/log)
        if self.minutos % 10 == 1:
            try:
                self.saldo = float(self.c.saldo())
            except Exception as e:  # noqa: BLE001
                log.warning("saldo indisponível: %s", e)
        self.noticias.atualizar()
        # monitor de invalidação
        if self.cfg["monitor"]["ativo"]:
            ids = self.auto.ids_ativos(agora) if self.auto else [self.est["id"]]
            for eid in ids:
                s = mon.avaliar(self.db.operacoes_df(self.conta, eid),
                                min_operacoes=self.cfg["monitor"]["min_operacoes"])
                if s.invalidada and not self.risco.parado_motivo:
                    self.risco.parar("monitor: " + s.texto)
                    self.notif.enviar("BOT PARADO pelo monitor: " + s.texto)
                    self.db.evento("ALERTA", s.texto)
                    break
        pode, motivo = self.risco.pode_operar(agora, self.saldo)
        self.status = "operando" if pode else f"PAUSADO — {motivo}"
        if pode:  # avisa no painel quando há notícia pausando algum par agora
            exp_ = int(self.est["params"].get("exp", 1))
            por_noticia: dict[str, list[str]] = {}
            for a in (getattr(self, "abertos", {}) or {}):
                msg = self.noticias.bloqueio(a, agora, exp_)
                if msg and msg.startswith("notícia"):
                    por_noticia.setdefault(msg, []).append(a.split("-")[0])
            if por_noticia:
                msg, pares = next(iter(por_noticia.items()))
                self.status = f"operando — {msg} ({len(pares)} pares em pausa)"
        if self.auto is not None:
            return self._passo_auto(agora, agora_ts)
        if agora_ts - self.ultima_selecao >= self.cfg["ativos"]["atualizar_lista_min"] * 60:
            try:
                self.selecionar_ativos()
            except Exception as e:  # noqa: BLE001
                log.error("falha ao selecionar ativos: %s", e)
                self.status = f"erro ao listar ativos: {e}"
                return
        if not self.selecionados and not self.status.startswith("PAUSADO"):
            self.status = getattr(self, "_motivo_vazio", "SEM ATIVOS")
        p = self.est["params"]
        exp = int(p["exp"])
        if self.est["familia"] == "humor":
            rev = self._reversao_op(agora)
            if rev:  # modo misto: reversão nos pares -op (horário comercial) + humor no OTC
                try:
                    self._passo_reversao_op(agora, agora_ts, rev)
                except Exception as e:  # noqa: BLE001
                    log.warning("reversão -op falhou: %s", e)
            # No horário da reversão, o humor só GRAVA (não opera). Exceção: fim de semana sem
            # nenhum par -op aberto. Em dia útil, mesmo se a IQ fechar os -op (ex.: depois de
            # notícia forte), o humor NÃO opera — foi o que causou o stop de 07/10 às 15h35.
            tem_op = any(a.endswith("-op") for a in (getattr(self, "abertos", {}) or {}))
            dia_util = agora.astimezone(FUSO_BR).weekday() < 5
            so_gravar = (bool(rev) and (tem_op or dia_util)
                         and not self.cfg["estrategia"]["reversao_op"].get("humor_junto", False))
            return self._passo_humor(agora, agora_ts, p, exp, so_otc=bool(rev), so_gravar=so_gravar)
        resumo = []  # batimento: o que aconteceu com cada ativo neste minuto
        for ativo, _nota, det in self.selecionados:
            if not self.exec.livre(ativo, agora_ts):
                resumo.append(f"{ativo[:6]}=ocupado")
                continue
            moeda = self._mesma_moeda(ativo, agora_ts, self.est["familia"])
            if moeda:
                resumo.append(f"{ativo[:6]}=pulado({moeda} em uso)")
                continue
            try:
                df = self.cache.atualizar(ativo)
            except Exception as e:  # noqa: BLE001
                log.warning("velas de %s falharam: %s", ativo, e)
                continue
            if not self.cache.fresco(ativo, agora_ts):
                log.info("%s: dados atrasados, pulando", ativo)
                resumo.append(f"{ativo[:6]}=atrasado")
                continue
            usd, expo = self._usd_index(ativo)
            d = st.decidir_agora(df, self.est["familia"], p, usd, expo)
            try:
                zk = float(st.calcular_features(df, self.est["familia"])[f"z{p.get('k', 5)}"].iloc[-1])
            except Exception:  # noqa: BLE001
                zk = float("nan")
            resumo.append(f"{ativo[:6]}={'SINAL' if d else 'sem'}(z={zk:+.1f})")
            if d == 0:
                continue
            lado = "CALL" if d > 0 else "PUT"
            self.sinais[ativo] = f"{lado} {agora.astimezone(FUSO_BR):%H:%M}"
            bloqueio = self.noticias.bloqueio(ativo, agora, exp)
            payout = det["payout_valor"]
            if not bloqueio and payout < self.cfg["ativos"]["payout_minimo"]:
                bloqueio = f"payout {payout:.0%} abaixo do mínimo"
            if not bloqueio:
                pode, motivo = self.risco.pode_operar(agora, self.saldo)
                bloqueio = None if pode else motivo
            if bloqueio:
                self.db.bloquear_sinal(ativo, lado, self.est["id"], bloqueio,
                                       em=agora.isoformat(timespec="seconds"))
                self._avisar_bloqueio(ativo, lado, bloqueio, agora)
                continue
            self.exec.enviar(ativo, d, exp, payout, self.est["id"], self.est["validada"])
        log.info("ciclo ok | %s | %s", self.status, " ".join(resumo) or "nenhum ativo")

    def _painel_thread(self, live, parar: threading.Event):
        """Atualiza o painel numa thread separada. Se o console travar (ex.: clique
        na janela do CMD com 'Edição Rápida'), só o PAINEL congela — o robô segue."""
        import dashboard
        while not parar.wait(2.0):
            try:
                live.update(dashboard.montar(self.estado()))
            except Exception:  # noqa: BLE001
                pass

    def _gravar_humor(self, agora, leituras: dict):
        """Grava o humor de TODOS os pares abertos a cada minuto (para validar a tática depois)."""
        pasta = self.pasta_dados / "humor"
        pasta.mkdir(parents=True, exist_ok=True)
        arq = pasta / f"humor_{agora:%Y-%m-%d}.csv"
        novo = not arq.exists()
        with open(arq, "a", encoding="utf-8") as fh:
            if novo:
                fh.write("time_utc,ativo,pct_call\n")
            for a, v in leituras.items():
                if v is not None:
                    fh.write(f"{agora:%Y-%m-%dT%H:%M:00},{a},{v:.4f}\n")

    def _avisar_bloqueio(self, ativo: str, lado: str, motivo: str, agora) -> None:
        """Mostra no painel e no log que um sinal foi BARRADO (notícia, payout, risco)."""
        self.sinais[ativo] = f"{lado} {agora.astimezone(FUSO_BR):%H:%M} BLOQUEADO"
        if not getattr(self, "_bloqueios", None):
            self._bloqueios = []
        self._bloqueios.append(f"{ativo[:6]} {lado}")
        if self.status.startswith("operando") or self.status.startswith("SINAL BLOQUEADO"):
            self.status = f"SINAL BLOQUEADO — {motivo}"

    def _mesma_moeda(self, ativo: str, agora_ts: float, familia: str) -> str | None:
        """Se já existe operação aberta com uma moeda deste par, devolve essa moeda.
        Evita duas apostas iguais (ex.: PUT USDBRL + PUT USDTRY = 2x 'dólar cai')."""
        regra = str(self.cfg["risco"].get("uma_por_moeda", "humor")).lower()
        if regra in ("nao", "não", "false", "0", "") or (regra == "humor" and familia != "humor"):
            return None
        from news import moedas_do_ativo
        minhas = moedas_do_ativo(ativo)
        for outro in set(self.exec.ocupado_ate) | set(self.exec.ocupados):
            if outro != ativo and not self.exec.livre(outro, agora_ts):
                comum = minhas & moedas_do_ativo(outro)
                if comum:
                    return sorted(comum)[0]
        return None

    def _reversao_op(self, agora) -> dict | None:
        """Parâmetros da reversão nos pares -op se o modo misto estiver ligado E for o horário."""
        rv = self.cfg["estrategia"].get("reversao_op") or {}
        if not rv.get("ativo"):
            return None
        ini, fim = rv.get("horas_brt", [9, 18])
        if not (int(ini) <= agora.astimezone(FUSO_BR).hour < int(fim)):
            return None
        p = dict(rv.get("params") or {})
        for k, v in {"k": 5, "z": 1.0, "regime": "todas", "lado": -1, "exp": 5}.items():
            p.setdefault(k, v)
        return p

    # ================================================================== automação
    def _passo_auto(self, agora, agora_ts):
        """Ciclo com automação: detecta a fase, aplica trocas pendentes e roda o(s) plano(s)."""
        from mercado import DESCRICAO, separar
        a = self.auto
        if agora_ts - a.ultima_checagem >= float(a.cfg["checar_fase_min"]) * 60:
            try:
                todos = self.c.ativos_abertos()
            except Exception as e:  # noqa: BLE001
                log.error("falha ao listar ativos: %s", e)
                self.status = f"erro ao listar ativos: {e}"
                return
            a.ultima_checagem = agora_ts
            fase, _ = a.observar(todos, agora)
            op, otc = separar(todos)
            self.abertos = {**op, **otc}
            log.info("fase %s | %d pares de mercado e %d OTC abertos", fase, len(op), len(otc))
            if a.precisa_reavaliar():
                a.reavaliar(self.c)
        sem_abertas = not self.exec.ocupados and self.risco.abertas == 0
        # mercado "livre" = o plano dele não está rodando agora e não há operação aberta nos pares dele:
        # a troca de estratégia desse mercado entra na hora (não precisa esperar o outro mercado parar)
        from mercado import tipo_par
        em_aberto = {x for x in set(self.exec.ocupados) | set(self.exec.ocupado_ate) if not self.exec.livre(x, agora_ts)}
        rodando = {p.mercado for p in a.planos_ativos(agora)}
        livres = {m for m in ("op", "otc") if m not in rodando and not any(tipo_par(x) == m for x in em_aberto)}
        for tr in a.efetivar(sem_abertas, agora, livres):
            self.db.evento("INFO", f"troca de estratégia {tr}")
        leituras = self._ler_humor(agora) if a.cfg.get("gravar_humor", True) else {}
        # Reabertura do OTC: roda numa thread própria (precisa de precisão de fração de segundo na
        # volta da pausa), independente da fase e JUNTO com os planos normais. Perto de cada janela
        # os planos normais não entram, para deixar as vagas de simultâneas livres para ela.
        import reabertura as rb
        rcfg = {**rb.PADRAO, **(self.cfg.get("reabertura") or {})}
        reab = bool(rcfg.get("ativo")) or a.estado_salvo["plano"].get("otc") == "reabertura_otc"
        if reab and not getattr(self.c, "sincrono", False):
            if getattr(self, "atirador", None) is None:
                self.atirador = rb.Atirador(self, rcfg)
            self.atirador.iniciar()
        reserva = rb.em_reserva(agora, rcfg["reserva_antes_s"], rcfg["reserva_depois_s"]) if reab else None
        planos = a.planos_ativos(agora)
        self.planos_atuais = planos
        resumo, esperando = [], []
        for pl in planos:
            if not pl.pode:
                esperando.append(pl.motivo)
                continue
            if reserva is not None and pl.estrategia.familia != "reabertura":
                esperando.append(f"vagas reservadas para a reabertura das {reserva.astimezone(FUSO_BR):%H:%M}")
                continue
            try:
                if pl.estrategia.familia == "reabertura":
                    at = getattr(self, "atirador", None)
                    resumo.append(at.status() if at else "Reabertura: aguardando")
                    esperando.append(at.status() if at else "Reabertura: aguardando a próxima janela")
                    continue
                if pl.estrategia.familia == "humor":
                    resumo.append(self._operar_humor(agora, agora_ts, pl, leituras))
                else:
                    resumo.append(self._passo_velas(agora, agora_ts, pl))
            except Exception as e:  # noqa: BLE001
                log.warning("plano %s falhou: %s", pl.estrategia.chave, e)
        if self.status == "operando" or self.status.startswith("operando —"):
            if not planos:
                self.status = "AGUARDANDO — " + DESCRICAO.get(a.detector.fase, "verificando o mercado")
            elif all((not p.pode) or p.estrategia.familia == "reabertura" for p in planos):
                self.status = "AGUARDANDO — " + "; ".join(esperando)
        if reab and getattr(self, "atirador", None) is not None and not any(
                p.estrategia.familia == "reabertura" for p in planos):
            resumo.append(self.atirador.status())
        log.info("ciclo ok | fase %s | %s | %s", a.detector.fase, self.status, " | ".join(resumo) or "-")

    def _ler_humor(self, agora) -> dict:
        """Lê e GRAVA o humor de todos os pares abertos (dados para validar o humor depois)."""
        leituras = {}
        for a in (getattr(self, "abertos", {}) or {}):
            try:
                leituras[a] = self.c.humor(a)
            except Exception:  # noqa: BLE001
                leituras[a] = None
        try:
            self._gravar_humor(agora, leituras)
        except Exception as e:  # noqa: BLE001
            log.warning("não gravei o humor: %s", e)
        return leituras

    def _pode_entrar(self, ativo, lado, payout, exp, eid, agora) -> bool:
        bloqueio = self.noticias.bloqueio(ativo, agora, exp)
        if not bloqueio and payout < self.cfg["ativos"]["payout_minimo"]:
            bloqueio = f"payout {payout:.0%} abaixo do mínimo"
        if not bloqueio:
            pode, motivo = self.risco.pode_operar(agora, self.saldo)
            bloqueio = None if pode else motivo
        if bloqueio:
            self.db.bloquear_sinal(ativo, lado, eid, bloqueio, em=agora.isoformat(timespec="seconds"))
            self._avisar_bloqueio(ativo, lado, bloqueio, agora)
            return False
        return True

    def _passo_velas(self, agora, agora_ts, pl) -> str:
        """Estratégias que decidem pelas velas (reversão, quadrante...) nos pares do plano."""
        e = pl.estrategia
        p = e.params_completos()
        exp = e.exp
        todos = getattr(self, "abertos", {}) or {}
        maximo = self.cfg["ativos"]["max_ativos_simultaneos"]
        resumo = []
        for a in sorted(pl.pares, key=lambda x: -pl.pares[x]):
            if sum(1 for x in todos if not self.exec.livre(x, agora_ts)) >= maximo:
                resumo.append("limite de simultâneas")
                break
            if not self.exec.livre(a, agora_ts):
                continue
            moeda = self._mesma_moeda(a, agora_ts, e.familia)
            if moeda:
                resumo.append(f"{a[:6]}=pulado({moeda} em uso)")
                continue
            df = self.cache.atualizar(a)
            if not self.cache.fresco(a, agora_ts):
                resumo.append(f"{a[:6]}=atrasado")
                continue
            ea = pl.por_par.get(a, e)               # variante aprovada neste par (ex.: confluência 4+)
            pa = p if ea is e else ea.params_completos()
            d = st.decidir_agora(df, ea.familia, pa)
            resumo.append(f"{a[:6]}={'SINAL' if d else 'sem'}" + ("" if ea is e else f"({ea.chave[-5:]})"))
            if d == 0:
                continue
            lado = "CALL" if d > 0 else "PUT"
            self.sinais[a] = f"{lado} {agora.astimezone(FUSO_BR):%H:%M} ({ea.nome})"
            if not self._pode_entrar(a, lado, pl.pares[a], ea.exp, ea.id, agora):
                resumo[-1] += "(BLOQUEADO)"
                continue
            self.exec.enviar(a, d, ea.exp, pl.pares[a], ea.id, pl.status == "aprovada")
        return f"{e.nome} [{pl.mercado}]: " + (" ".join(resumo) or "nenhum par")

    def _operar_humor(self, agora, agora_ts, pl, leituras) -> str:
        e = pl.estrategia
        limite = float(e.params.get("limite", 0.7))
        exp = e.exp
        todos = getattr(self, "abertos", {}) or {}
        # Leituras de exatamente 0% ou 100% não são confiáveis (quase ninguém operando o par): no
        # histórico gravado de 07/10, ir contra elas acertou só 42,8% (n=138). Ficam de fora.
        cand = sorted(((a, v) for a, v in leituras.items()
                       if v is not None and 0.0 < v < 1.0 and a in pl.pares),
                      key=lambda x: -abs(x[1] - 0.5))
        resumo = [f"{a[:6]}={v:.0%}" for a, v in cand[:4]]
        maximo = self.cfg["ativos"]["max_ativos_simultaneos"]
        entradas = 0
        ocupados = sum(1 for a in todos if not self.exec.livre(a, agora_ts))
        for a, v in cand:
            if ocupados + entradas >= maximo:
                break
            d = st.decidir_humor(v, limite)
            if d == 0:
                break
            if not self.exec.livre(a, agora_ts):
                continue
            moeda = self._mesma_moeda(a, agora_ts, "humor")
            if moeda:
                resumo.append(f"[{a[:6]} pulado: já há operação com {moeda}]")
                continue
            lado = "CALL" if d > 0 else "PUT"
            self.sinais[a] = f"{lado} {agora.astimezone(FUSO_BR):%H:%M} ({v:.0%} em CALL)"
            if not self._pode_entrar(a, lado, pl.pares[a], exp, e.id, agora):
                continue
            if self.exec.enviar(a, d, exp, pl.pares[a], e.id, pl.status == "aprovada"):
                entradas += 1
        return f"{e.nome} [{pl.mercado}]: " + (" ".join(resumo) or "sem leitura ainda")

    def _passo_reversao_op(self, agora, agora_ts, p):
        abertos = getattr(self, "abertos", {}) or {}
        pares = sorted((a for a in abertos if a.endswith("-op")), key=lambda a: -abertos[a])
        id_rev = f"regime:{json.dumps(p, sort_keys=True)}"
        exp = int(p["exp"])
        maximo = self.cfg["ativos"]["max_ativos_simultaneos"]
        resumo = []
        for a in pares:
            ocupados = sum(1 for x in abertos if not self.exec.livre(x, agora_ts))
            if ocupados >= maximo:
                resumo.append("limite de simultâneas")
                break
            if not self.exec.livre(a, agora_ts):
                continue
            moeda = self._mesma_moeda(a, agora_ts, "regime")
            if moeda:
                resumo.append(f"{a[:6]}=pulado({moeda} em uso)")
                continue
            df = self.cache.atualizar(a)
            if not self.cache.fresco(a, agora_ts):
                resumo.append(f"{a[:6]}=atrasado")
                continue
            d = st.decidir_agora(df, "regime", p)
            resumo.append(f"{a[:6]}={'SINAL' if d else 'sem'}")
            if d == 0:
                continue
            lado = "CALL" if d > 0 else "PUT"
            self.sinais[a] = f"{lado} {agora.astimezone(FUSO_BR):%H:%M} (reversão)"
            payout = abertos[a]
            bloqueio = self.noticias.bloqueio(a, agora, exp)
            if not bloqueio and payout < self.cfg["ativos"]["payout_minimo"]:
                bloqueio = f"payout {payout:.0%} abaixo do mínimo"
            if not bloqueio:
                pode, motivo = self.risco.pode_operar(agora, self.saldo)
                bloqueio = None if pode else motivo
            if bloqueio:
                self.db.bloquear_sinal(a, lado, id_rev, bloqueio, em=agora.isoformat(timespec="seconds"))
                self._avisar_bloqueio(a, lado, bloqueio, agora)
                resumo[-1] += "(BLOQUEADO)"
                continue
            self.exec.enviar(a, d, exp, payout, id_rev, False)
        log.info("reversão -op | %s", " ".join(resumo) or "nenhum par -op aberto")

    def _passo_humor(self, agora, agora_ts, p, exp, so_otc: bool = False, so_gravar: bool = False):
        abertos = getattr(self, "abertos", {}) or {}
        leituras = {}
        for a in abertos:
            try:
                leituras[a] = self.c.humor(a)
            except Exception:  # noqa: BLE001
                leituras[a] = None
        try:
            self._gravar_humor(agora, leituras)
        except Exception as e:  # noqa: BLE001
            log.warning("não gravei o humor: %s", e)
        if so_gravar:
            log.info("ciclo ok | %s | humor só gravando (%d pares lidos) — horário da reversão nos pares -op",
                     self.status, sum(v is not None for v in leituras.values()))
            return
        limite = float(p.get("limite", 0.7))
        # mais extremos primeiro
        cand = sorted(((a, v) for a, v in leituras.items()
                       if v is not None and not (so_otc and a.endswith("-op"))),
                      key=lambda x: -abs(x[1] - 0.5))
        resumo = [f"{a[:6]}={v:.0%}" for a, v in cand[:4]]
        ocupados = sum(1 for a in abertos if not self.exec.livre(a, agora_ts))
        maximo = self.cfg["ativos"]["max_ativos_simultaneos"]
        entradas = 0
        for a, v in cand:
            if ocupados + entradas >= maximo:
                break
            d = st.decidir_humor(v, limite)
            if d == 0:
                break  # lista ordenada: os próximos são menos extremos
            if not self.exec.livre(a, agora_ts):
                continue
            moeda = self._mesma_moeda(a, agora_ts, "humor")
            if moeda:
                resumo.append(f"[{a[:6]} pulado: já há operação com {moeda}]")
                continue
            payout = abertos.get(a, 0.0)
            lado = "CALL" if d > 0 else "PUT"
            self.sinais[a] = f"{lado} {agora.astimezone(FUSO_BR):%H:%M} ({v:.0%} em CALL)"
            bloqueio = self.noticias.bloqueio(a, agora, exp)
            if not bloqueio and payout < self.cfg["ativos"]["payout_minimo"]:
                bloqueio = f"payout {payout:.0%} abaixo do mínimo"
            if not bloqueio:
                pode, motivo = self.risco.pode_operar(agora, self.saldo)
                bloqueio = None if pode else motivo
            if bloqueio:
                self.db.bloquear_sinal(a, lado, self.est["id"], bloqueio, em=agora.isoformat(timespec="seconds"))
                self._avisar_bloqueio(a, lado, bloqueio, agora)
                continue
            if self.exec.enviar(a, d, exp, payout, self.est["id"], self.est["validada"]):
                entradas += 1
        log.info("ciclo ok | %s | humor (mais extremos): %s | %d pares lidos", self.status,
                 " ".join(resumo) or "sem leitura ainda", sum(v is not None for v in leituras.values()))

    def rodar(self, max_minutos: int | None = None, atraso: float | None = None):
        atraso = self.cfg["estrategia"]["atraso_entrada_s"] if atraso is None else atraso
        live = None
        parar_painel = threading.Event()
        if self.painel:
            from rich.live import Live

            import dashboard
            live = Live(dashboard.montar(self.estado()), refresh_per_second=1, screen=False)
            live.start()
            threading.Thread(target=self._painel_thread, args=(live, parar_painel), daemon=True).start()
        n = 0
        # Vigia: se um ciclo passar de 3 min, grava onde o programa travou (logs/travamento.log)
        vigia = None
        if isinstance(self.rel, RelogioReal) and not isinstance(self.rel, RelogioFalso):
            import faulthandler
            (RAIZ / "logs").mkdir(exist_ok=True)
            vigia = open(RAIZ / "logs" / "travamento.log", "a", encoding="utf-8")
        try:
            while max_minutos is None or n < max_minutos:
                if vigia:
                    faulthandler.dump_traceback_later(180, repeat=True, file=vigia)
                agora = self.rel.agora()
                prox = (agora // 60 + 1) * 60 + atraso
                while self.rel.agora() < prox:
                    self.rel.dormir(min(1.0, prox - self.rel.agora()))
                try:
                    self.passo()
                except Exception as e:  # noqa: BLE001  (o bot não pode morrer por um erro pontual)
                    log.exception("erro no passo: %s", e)
                    self.status = f"erro: {e}"
                if not self.painel and isinstance(self.rel, RelogioReal) and not isinstance(self.rel, RelogioFalso):
                    print(f"{self.rel.agora_dt().astimezone(FUSO_BR):%H:%M:%S} {self.status} | "
                          f"hoje {self.risco.lucro_dia:+.2f}")
                n += 1
        finally:
            if vigia:
                faulthandler.cancel_dump_traceback_later()
                vigia.close()
            parar_painel.set()
            if live:
                live.stop()

def desativar_edicao_rapida():
    """No Windows, clicar na janela do CMD com a 'Edição Rápida' ligada PAUSA o programa
    até apertar Enter/Esc. Desligamos isso só para esta janela."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-10)  # entrada do console
        modo = ctypes.c_uint32()
        if k32.GetConsoleMode(h, ctypes.byref(modo)):
            k32.SetConsoleMode(h, (modo.value & ~0x0040) | 0x0080)  # tira QUICK_EDIT, mantém EXTENDED_FLAGS
    except Exception:  # noqa: BLE001
        pass


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--simular", action="store_true", help="corretora falsa, sem internet")
    ap.add_argument("--minutos", type=int, default=None, help="para depois de N minutos")
    ap.add_argument("--config", default=None)
    ap.add_argument("--sem-painel", action="store_true")
    a = ap.parse_args(argv)

    configurar_log(RAIZ / "logs")
    desativar_edicao_rapida()
    cfg = configuracao.carregar(a.config)
    estrat = carregar_estrategia(cfg)
    print(f"Estratégia: {estrat['familia']} {estrat['params']} — origem: {estrat['origem']}"
          f" — {'VALIDADA' if estrat['validada'] else 'NÃO VALIDADA'}")

    if a.simular:
        import time
        rel = RelogioFalso(time.time())
        corr = CorretoraFalsa(rel)
        conta = "SIMULACAO"
        if cfg.get("automacao"):
            cfg["automacao"]["reavaliar_horas"] = 0   # simulação não reavalia (velas falsas)
        banco = BancoDados(RAIZ / "data" / "simulacao.db")
        bot = Bot(cfg, corr, rel, banco, conta, estrat, pasta_stop=RAIZ, pasta_dados=RAIZ / "data" / "simulacao")
        bot.rodar(a.minutos or 600)
        bot.exec.aguardar_todas(5)
        df = banco.operacoes_df(conta)
        print(f"Simulação: {len(df)} operações registradas em data/simulacao.db")
        for eid, g in df.groupby("estrategia"):
            print(f"  {eid.split(':')[0]}: {mon.avaliar(g).texto}")
        return bot

    from dotenv import load_dotenv

    load_dotenv(RAIZ / ".env")
    from connection import CorretoraIQ

    conta = cfg["conta"]["tipo"]
    from trava import MENSAGEM, Trava
    trava = Trava(RAIZ / "data" / "bot.lock")
    if not trava.adquirir():  # a interface (app.py) ou outro CMD já está rodando
        print(MENSAGEM)
        sys.exit(1)
    if conta == "REAL" and not confirmar_real(cfg, estrat):
        print("Frase incorreta. Nada foi feito.")
        sys.exit(1)
    corr = CorretoraIQ(timeout=cfg["conexao"]["timeout_s"], max_tentativas=cfg["conexao"]["max_tentativas"])
    print("Conectando... (feche o app da IQ e outros scripts na mesma conta: eles derrubam a sessão)")
    corr.conectar(conta)
    rel = RelogioReal(corr.hora_servidor() - __import__("time").time())
    banco = BancoDados(RAIZ / "data" / "bot.db")
    noticias = FiltroNoticias(cfg["noticias"], RAIZ / "data" / "noticias")
    notif = Notificador(cfg["telegram"]["ativo"])
    bot = Bot(cfg, corr, rel, banco, conta, estrat, noticias, notif, RAIZ,
              painel=cfg["painel"]["ativo"] and not a.sem_painel)
    notif.enviar(f"Bot iniciado ({conta}) — {estrat['id']}")
    try:
        bot.rodar(a.minutos)
    except KeyboardInterrupt:
        print("\nParando... aguardando ordens abertas terminarem (até 6 min). Ctrl+C de novo força a saída.")
        try:
            bot.exec.aguardar_todas(400)
        except KeyboardInterrupt:
            pass
    import report
    csv, html = report.operacoes(banco, RAIZ / "reports")
    print(f"Relatório: {html}")


if __name__ == "__main__":
    main()
