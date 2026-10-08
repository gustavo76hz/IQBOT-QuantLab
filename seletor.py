"""Seletor automático: decide ONDE (pares) e COMO (estratégia) o bot opera em cada fase.

Existe um PLANO para pares de mercado ('op') e um para OTC ('otc'). A fase do mercado
(mercado.py) decide qual plano roda. Os modos (config automacao.modo):

  manual  nada muda sozinho: estratégias e pares são os que você escolheu; o app só RECOMENDA
  semi    (padrão) os pares de uma estratégia APROVADA seguem a avaliação automaticamente;
          trocar de estratégia só com o seu "Aplicar" no app
  auto    além disso, troca sozinho para a melhor estratégia APROVADA

Em todos os modos: troca só acontece SEM operação aberta; nunca mexe no stop, nos limites
do dia nem no orçamento (o GestorRisco é o mesmo). Toda troca fica registrada no banco.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import avaliador
import catalogo
from mercado import DESCRICAO, DetectorFase, separar
from risk import FUSO_BR

log = logging.getLogger("bot.auto")
RAIZ = Path(__file__).parent

PADRAO = {"ativo": True, "modo": "semi", "checar_fase_min": 3, "leituras_para_trocar": 2,
          "misto_usa_otc": False, "plano_inicial": {"op": "reversao_5m", "otc": "humor_70"},
          "permitir_em_teste": True, "em_teste_otc_horario_comercial": False,
          "pares_op": [], "pares_otc": [], "payout_minimo_otc": 0, "reavaliar_horas": 24, "ignorar_horas": 24,
          "aprovacao": {}}
NOMES_MERCADO = {"op": "pares de mercado", "otc": "OTC"}


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%".replace(".", ",")


def _int(n: int) -> str:
    return f"{n:,}".replace(",", ".")


@dataclass
class Plano:
    mercado: str
    estrategia: catalogo.Estrategia
    status: str                  # aprovada | em_teste | reprovada | sem_avaliacao
    pares: dict = field(default_factory=dict)     # {ativo: payout} que vai operar agora
    pode: bool = True
    motivo: str = ""
    por_par: dict = field(default_factory=dict)   # {ativo: Estrategia} quando o par usa outra variante

    def resumo(self) -> dict:
        return {"mercado": self.mercado, "chave": self.estrategia.chave, "nome": self.estrategia.nome,
                "status": self.status, "pares": sorted(self.pares), "pode": self.pode, "motivo": self.motivo}


class Automacao:
    def __init__(self, cfg: dict, pasta_dados: Path, banco, corretora=None):
        self.cfg_total = cfg
        self.cfg = {**PADRAO, **(cfg.get("automacao") or {})}
        if self.cfg["modo"] not in ("manual", "semi", "auto"):
            raise ValueError("automacao.modo deve ser manual, semi ou auto")
        self.pasta = Path(pasta_dados)
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.db = banco
        self.c = corretora
        self.detector = DetectorFase(int(self.cfg["leituras_para_trocar"]))
        self.arq_estado = self.pasta / "automacao.json"
        self.arq_aval = self.pasta / "avaliacao.json"
        self.arq_prog = self.pasta / "avaliacao_progresso.json"
        self.arq_payouts = self.pasta / "payouts.json"
        self.estado_salvo = self._ler_estado()
        self._aval = None
        self._aval_mtime = None
        self._thread_aval: threading.Thread | None = None
        self.parar_aval = threading.Event()
        self.ultima_checagem = -1e18
        self.payouts: dict[str, float] = {}
        self.avisos: list[dict] = []           # notificações para o app (fase mudou, troca feita...)

    # ------------------------------------------------------------------ estado em disco
    def _ler_estado(self) -> dict:
        try:
            d = json.loads(self.arq_estado.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            d = {}
        plano = {**self.cfg["plano_inicial"], **(d.get("plano") or {})}
        for m, ch in list(plano.items()):           # chave inválida no arquivo -> volta ao config
            if ch not in catalogo.POR_CHAVE or m not in catalogo.POR_CHAVE[ch].mercados:
                plano[m] = self.cfg["plano_inicial"].get(m)
        return {"plano": plano, "pendente": d.get("pendente") or {}, "ignoradas": d.get("ignoradas") or {}}

    def _salvar_estado(self):
        tmp = self.arq_estado.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.estado_salvo, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.arq_estado)

    # ------------------------------------------------------------------ avaliação
    def avaliacao(self) -> dict | None:
        try:
            mt = self.arq_aval.stat().st_mtime if self.arq_aval.exists() else None
        except OSError:
            mt = None
        if mt != self._aval_mtime:
            self._aval, self._aval_mtime = avaliador.carregar(self.arq_aval), mt
        return self._aval

    def status_estrategia(self, chave: str, mercado: str) -> tuple[str, list[str], dict | None]:
        """(status, pares aprovados, números somados dos pares aprovados)."""
        e = catalogo.obter(chave)
        if not e.validavel:
            return "em_teste", [], None
        av = self.avaliacao()
        if not av:
            return "sem_avaliacao", [], None
        d = (av.get("resumo", {}).get(mercado) or {}).get(chave)
        if not d:
            return "sem_avaliacao", [], None
        if d["pares_aprovados"]:
            return "aprovada", list(d["pares_aprovados"]), d
        return "reprovada", [], d

    # ------------------------------------------------------------------ fase
    def observar(self, abertos: dict[str, float], agora: datetime) -> tuple[str, bool]:
        anterior = self.detector.fase
        fase = self.detector.atualizar(abertos)
        self.payouts = dict(abertos)
        try:
            tmp = self.arq_payouts.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.payouts), encoding="utf-8")
            tmp.replace(self.arq_payouts)
        except OSError:
            pass
        if self.detector.trocou and anterior is not None:
            self._avisar(f"Fase do mercado mudou: {DESCRICAO[anterior]} → {DESCRICAO[fase]}.", "fase")
            log.info("fase do mercado: %s -> %s", anterior, fase)
        return fase, self.detector.trocou

    def mercados_ativos(self) -> list[str]:
        return self.detector.mercados_ativos(bool(self.cfg["misto_usa_otc"]))

    # ------------------------------------------------------------------ plano
    def plano(self, mercado: str, agora: datetime) -> Plano:
        e = catalogo.obter(self.estado_salvo["plano"][mercado])
        status, aprovados, _ = self.status_estrategia(e.chave, mercado)
        abertos = self.detector.op if mercado == "op" else self.detector.otc
        fixos = self.cfg["pares_op"] if mercado == "op" else self.cfg["pares_otc"]
        if not fixos and mercado == "op":
            fixos = self.cfg_total["ativos"]["lista"]
        por_par = {}
        if self.cfg["modo"] in ("semi", "auto") and e.familia == "confluencia":
            # Confluência: cada par usa a variante (4+ ou 5+ sinais) que foi APROVADA nele. A
            # escolhida no plano tem prioridade; as outras só entram nos pares em que ela não passou.
            for x in [e] + [x for x in catalogo.para_mercado(mercado) if x.familia == e.familia and x.chave != e.chave]:
                sx, apx, _ = self.status_estrategia(x.chave, mercado)
                if sx == "aprovada":
                    for a in apx:
                        if a not in aprovados and a not in por_par:
                            por_par[a] = x
            if por_par:
                aprovados = list(aprovados) + sorted(por_par)
                status = "aprovada"
        if status == "aprovada" and self.cfg["modo"] in ("semi", "auto"):
            base = aprovados                         # pares seguem a avaliação
        elif fixos:
            base = fixos                             # pares escolhidos por você
        else:
            base = list(abertos)                     # OTC sem lista: todos os pares de moedas abertos
        pares = {a: abertos[a] for a in base if a in abertos}
        pmin = float(self.cfg.get("payout_minimo_otc") or 0)
        sem_payout = False
        if mercado == "otc" and pmin > 0:                 # só os pares OTC de payout mais alto
            sem_payout = bool(pares)
            pares = {a: v for a, v in pares.items() if v >= pmin - 1e-9}
            sem_payout = sem_payout and not pares
        pl = Plano(mercado, e, status, pares, por_par={a: x for a, x in por_par.items() if a in pares})
        hora = agora.astimezone(FUSO_BR)
        if status in ("reprovada", "sem_avaliacao", "em_teste") and not self.cfg["permitir_em_teste"]:
            pl.pode, pl.motivo = False, f"Sem estratégia aprovada para {NOMES_MERCADO[mercado]} agora — só gravando dados"
        elif (status != "aprovada" and mercado == "otc" and not self.cfg["em_teste_otc_horario_comercial"]
              and hora.weekday() < 5 and 9 <= hora.hour < 18):
            pl.pode, pl.motivo = False, ("Estratégia do OTC ainda não aprovada: em dia útil, das 9h às 18h, "
                                         "ela só grava dados")
        elif not e.no_horario(mercado, hora.hour):
            h = e.horas_brt[mercado]
            pl.pode, pl.motivo = False, f"{e.nome} só opera das {h[0]}h às {h[1]}h"
        elif sem_payout:
            pl.pode, pl.motivo = False, f"Nenhum par OTC com payout de {pmin:.0%} ou mais agora"
        elif not pares:
            pl.pode, pl.motivo = False, f"Nenhum par da estratégia aberto em {NOMES_MERCADO[mercado]}"
        return pl

    def planos_ativos(self, agora: datetime) -> list[Plano]:
        planos = [self.plano(m, agora) for m in self.mercados_ativos()]
        # MISTO (pares de mercado e OTC abertos): se o plano de mercado não pode operar agora
        # (fora do horário dele, sem par aberto...), o plano do OTC assume em vez de ficar parado
        if (self.detector.fase == "MISTO" and not self.cfg["misto_usa_otc"]
                and planos and planos[0].mercado == "op" and not planos[0].pode):
            planos.append(self.plano("otc", agora))
        return planos

    def ids_ativos(self, agora: datetime) -> list[str]:
        return [p.estrategia.id for p in self.planos_ativos(agora)]

    # ------------------------------------------------------------------ recomendações
    def recomendacoes(self, agora: datetime | None = None) -> list[dict]:
        out = []
        agora = agora or datetime.now(timezone.utc)
        for mercado in ("op", "otc"):
            atual = self.estado_salvo["plano"][mercado]
            st_atual, _, d_atual = self.status_estrategia(atual, mercado)
            melhor, d_melhor = None, None
            for e in catalogo.para_mercado(mercado):
                s, _, d = self.status_estrategia(e.chave, mercado)
                if s == "aprovada" and (d_melhor is None or d["ic"][0] > d_melhor["ic"][0]):
                    melhor, d_melhor = e, d
            if melhor is None or melhor.chave == atual:
                continue
            if st_atual == "aprovada" and d_melhor["ic"][0] <= d_atual["ic"][0] + 0.005:
                continue                              # não é claramente melhor
            chave_rec = f"{mercado}|{melhor.chave}"
            ate = self.estado_salvo["ignoradas"].get(chave_rec)
            if ate and datetime.fromisoformat(ate) > agora:
                continue
            nome_atual = catalogo.obter(atual).nome
            if st_atual == "aprovada":
                porque = f"tem resultado mais forte que {nome_atual}"
            else:
                porque = f"a atual ({nome_atual}) não está aprovada" + (" — está em teste" if st_atual == "em_teste" else "")
            out.append({"id": chave_rec, "mercado": mercado, "de": atual, "de_nome": nome_atual,
                        "para": melhor.chave, "para_nome": melhor.nome,
                        "acerto": d_melhor["acerto"], "ic": d_melhor["ic"], "n": d_melhor["n"],
                        "pares": d_melhor["pares_aprovados"], "porque": porque,
                        "texto": (f"Em {NOMES_MERCADO[mercado]}, {melhor.nome} está aprovada com "
                                  f"{_pct(d_melhor['acerto'])} [{_pct(d_melhor['ic'][0])}–{_pct(d_melhor['ic'][1])}] em "
                                  f"{_int(d_melhor['n'])} operações simuladas ({len(d_melhor['pares_aprovados'])} pares); "
                                  f"{porque}.")})
        return out

    def aplicar(self, mercado: str, chave: str, quem: str = "você") -> None:
        e = catalogo.obter(chave)
        if mercado not in e.mercados:
            raise ValueError(f"{e.nome} não opera em {NOMES_MERCADO.get(mercado, mercado)}")
        if quem != "você" and self.status_estrategia(chave, mercado)[0] != "aprovada":
            raise ValueError("troca automática só para estratégia aprovada")
        self.estado_salvo["pendente"][mercado] = {"chave": chave, "quem": quem,
                                                  "em": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self._salvar_estado()
        log.info("troca pedida (%s): %s -> %s (%s)", mercado, self.estado_salvo["plano"][mercado], chave, quem)

    def ignorar(self, rec_id: str, agora: datetime | None = None) -> None:
        agora = agora or datetime.now(timezone.utc)
        ate = agora + timedelta(hours=float(self.cfg["ignorar_horas"]))
        self.estado_salvo["ignoradas"][rec_id] = ate.isoformat(timespec="seconds")
        self._salvar_estado()

    def efetivar(self, sem_operacao_aberta: bool, agora: datetime, livres=()) -> list[dict]:
        """Aplica as trocas pendentes (e, no modo auto, as recomendações).

        Uma troca entra quando não há nenhuma operação aberta, OU na hora quando o mercado dela
        está em `livres` (o plano desse mercado não está rodando e não tem operação aberta dele —
        ex.: trocar a estratégia dos pares de mercado à noite, com só o OTC aberto)."""
        if self.cfg["modo"] == "auto":
            for r in self.recomendacoes(agora):
                if self.estado_salvo["pendente"].get(r["mercado"], {}).get("chave") != r["para"]:
                    self.aplicar(r["mercado"], r["para"], quem="automático")
        pend = {m: p for m, p in self.estado_salvo["pendente"].items() if sem_operacao_aberta or m in set(livres)}
        if not pend:
            return []
        feitas = []
        for mercado, p in pend.items():
            de = self.estado_salvo["plano"][mercado]
            self.estado_salvo["plano"][mercado] = p["chave"]
            s, _, d = self.status_estrategia(p["chave"], mercado)
            motivo = (f"{catalogo.obter(p['chave']).nome}: {s}"
                      + (f", {_pct(d['acerto'])} em {_int(d['n'])} operações simuladas" if d and d.get("acerto") else ""))
            if self.db is not None:
                self.db.registrar_troca(mercado, de, p["chave"], motivo, p["quem"], em=agora.isoformat(timespec="seconds"))
            feitas.append({"mercado": mercado, "de": de, "para": p["chave"], "quem": p["quem"]})
            self._avisar(f"Estratégia de {NOMES_MERCADO[mercado]} trocada para {catalogo.obter(p['chave']).nome} "
                         f"({p['quem']}).", "troca")
            log.info("troca efetivada (%s): %s -> %s por %s", mercado, de, p["chave"], p["quem"])
            self.estado_salvo["pendente"].pop(mercado, None)
        self._salvar_estado()
        return feitas

    # ------------------------------------------------------------------ reavaliação em segundo plano
    def avaliando(self) -> bool:
        return bool(self._thread_aval and self._thread_aval.is_alive())

    def progresso(self) -> dict:
        try:
            return json.loads(self.arq_prog.read_text(encoding="utf-8")) if self.arq_prog.exists() else {}
        except Exception:  # noqa: BLE001
            return {}

    def precisa_reavaliar(self) -> bool:
        h = float(self.cfg["reavaliar_horas"] or 0)
        if h <= 0 or self.avaliando():
            return False
        idade = avaliador.idade_horas(self.avaliacao())
        return idade is None or idade >= h

    def reavaliar(self, corretora=None, baixar: bool = True) -> bool:
        """Baixa as velas que faltam (pela conexão do bot) e roda o cálculo em OUTRO processo."""
        if self.avaliando():
            return False
        corretora = corretora or self.c
        pasta_velas = self.pasta / "iq"
        ativos = sorted(set(self.detector.op) | set(self.detector.otc)
                        | {p.name[:-7] for p in pasta_velas.glob("*.csv.gz")} if pasta_velas.exists()
                        else set(self.detector.op) | set(self.detector.otc))
        regras = json.dumps(self.cfg.get("aprovacao") or {})

        def trabalho():
            try:
                avaliador.gravar_progresso(self.arq_prog, rodando=True, etapa="começando", feito=0, total=1,
                                           erro="", inicio=datetime.now(timezone.utc).isoformat(timespec="seconds"))
                if baixar and corretora is not None and ativos:
                    avaliador.atualizar_velas(corretora, ativos, pasta_velas, self.arq_prog, parar=self.parar_aval)
                if self.parar_aval.is_set():
                    avaliador.gravar_progresso(self.arq_prog, rodando=False, etapa="cancelado")
                    return
                flags = 0x08000000 if sys.platform == "win32" else 0     # sem janela de console
                cmd = [sys.executable, str(RAIZ / "avaliador.py"), "--calcular", "--pasta", str(pasta_velas),
                       "--saida", str(self.arq_aval), "--payouts", str(self.arq_payouts),
                       "--progresso", str(self.arq_prog), "--regras", regras]
                r = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags, timeout=3600)
                if r.returncode != 0:
                    raise RuntimeError((r.stderr or "").strip().splitlines()[-1] if r.stderr else "falhou")
                self._avisar("Avaliação das estratégias concluída.", "avaliacao")
                log.info("avaliação concluída")
            except Exception as e:  # noqa: BLE001
                avaliador.gravar_progresso(self.arq_prog, rodando=False, etapa="erro", erro=str(e))
                log.warning("avaliação falhou: %s", e)

        self._thread_aval = threading.Thread(target=trabalho, daemon=True)
        self._thread_aval.start()
        return True

    # ------------------------------------------------------------------ app
    def _avisar(self, texto: str, tipo: str):
        self.avisos.append({"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "texto": texto, "tipo": tipo})
        self.avisos = self.avisos[-30:]

    def estado(self, agora: datetime) -> dict:
        av = self.avaliacao()
        combos = (av or {}).get("combinacoes", [])
        planos = {m: self.plano(m, agora).resumo() for m in ("op", "otc")}
        ativos_m = [p.mercado for p in self.planos_ativos(agora)]
        for m, p in planos.items():
            p["rodando_agora"] = m in ativos_m
            p["pendente"] = (self.estado_salvo["pendente"].get(m) or {}).get("chave")
        return {
            "modo": self.cfg["modo"], "fase": self.detector.fase,
            "fase_texto": DESCRICAO.get(self.detector.fase, "Ainda não verificado"),
            "n_op": len(self.detector.op), "n_otc": len(self.detector.otc),
            "planos": planos, "recomendacoes": self.recomendacoes(agora),
            "avaliacao": {"gerado_em": (av or {}).get("gerado_em"), "idade_horas": avaliador.idade_horas(av),
                          "regras": (av or {}).get("regras"), "avaliando": self.avaliando(),
                          "progresso": self.progresso()},
            "pares": [{"ativo": x["ativo"], "mercado": x["mercado"], "chave": x["chave"], "n": x["n"],
                       "acerto": x["acerto"], "ic": x["ic"], "elegivel": x["elegivel"], "motivos": x["motivos"]}
                      for x in combos if x["chave"] in (planos["op"]["chave"], planos["otc"]["chave"])],
            "catalogo": [{"chave": e.chave, "nome": e.nome, "mercados": list(e.mercados), "validavel": e.validavel,
                          "status": {m: self.status_estrategia(e.chave, m)[0] for m in e.mercados}}
                         for e in catalogo.CATALOGO],
            "avisos": list(self.avisos),
        }
