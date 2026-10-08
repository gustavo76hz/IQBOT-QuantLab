"""Detector de FASE do mercado: o que a IQ Option tem aberto agora.

Fases:
  MERCADO  só pares de mercado (-op) abertos
  MISTO    pares -op e -OTC abertos (o normal durante o dia)
  OTC      só pares -OTC (noite, fim de semana, ou a IQ suspendeu os -op)
  FECHADO  nenhum par de moedas aberto

Usa a disponibilidade REAL que a corretora devolve (não o relógio). Para não pular
de fase por causa de uma leitura isolada (um par que some por 1 minuto), a troca só
acontece depois de N leituras seguidas iguais (histerese, padrão 2).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MOEDAS = "USD|EUR|GBP|JPY|AUD|NZD|CAD|CHF|SEK|NOK|SGD|HKD|MXN|TRY|ZAR|PLN|CNH|DKK|HUF|CZK|INR|BRL|THB|ILS"
PAR_OP = re.compile(rf"({MOEDAS})({MOEDAS})-op")
PAR_OTC = re.compile(rf"({MOEDAS})({MOEDAS})-OTC")

FASES = ("MERCADO", "MISTO", "OTC", "FECHADO")
DESCRICAO = {
    "MERCADO": "Pares de mercado abertos",
    "MISTO": "Pares de mercado e OTC abertos",
    "OTC": "Só OTC aberto (pares de mercado fechados)",
    "FECHADO": "Nenhum par de moedas aberto",
}


def tipo_par(ativo: str) -> str | None:
    """'op' para par de mercado, 'otc' para OTC, None para o resto (cripto, índices...)."""
    if PAR_OP.fullmatch(ativo):
        return "op"
    if PAR_OTC.fullmatch(ativo):
        return "otc"
    return None


def separar(abertos: dict[str, float]) -> tuple[dict[str, float], dict[str, float]]:
    """{ativo: payout} -> (pares -op abertos, pares -OTC abertos), só moedas."""
    op = {a: p for a, p in abertos.items() if tipo_par(a) == "op"}
    otc = {a: p for a, p in abertos.items() if tipo_par(a) == "otc"}
    return op, otc


def classificar(n_op: int, n_otc: int) -> str:
    if n_op and n_otc:
        return "MISTO"
    if n_op:
        return "MERCADO"
    if n_otc:
        return "OTC"
    return "FECHADO"


@dataclass
class DetectorFase:
    leituras_para_trocar: int = 2
    fase: str | None = None             # fase confirmada
    candidata: str | None = None
    contagem: int = 0
    op: dict = field(default_factory=dict)
    otc: dict = field(default_factory=dict)
    trocou: bool = False                 # a última leitura mudou a fase confirmada?

    def atualizar(self, abertos: dict[str, float]) -> str:
        """Recebe {ativo: payout} dos ativos abertos e devolve a fase confirmada."""
        self.op, self.otc = separar(abertos)
        lida = classificar(len(self.op), len(self.otc))
        self.trocou = False
        if self.fase is None:            # primeira leitura: aceita direto
            self.fase, self.candidata, self.contagem = lida, None, 0
            self.trocou = True
            return self.fase
        if lida == self.fase:
            self.candidata, self.contagem = None, 0
            return self.fase
        if lida == self.candidata:
            self.contagem += 1
        else:
            self.candidata, self.contagem = lida, 1
        if self.contagem >= self.leituras_para_trocar:
            self.fase, self.candidata, self.contagem = lida, None, 0
            self.trocou = True
        return self.fase

    def mercados_ativos(self, misto_usa_otc: bool = False) -> list[str]:
        """Quais planos rodam na fase atual: 'op', 'otc' ou os dois."""
        if self.fase == "MERCADO":
            return ["op"]
        if self.fase == "MISTO":
            return ["op", "otc"] if misto_usa_otc else ["op"]
        if self.fase == "OTC":
            return ["otc"]
        return []
