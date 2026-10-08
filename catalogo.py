"""Catálogo: todas as estratégias que o bot sabe operar, com os seus metadados.

As REGRAS de decisão continuam em strategy.py (a mesma função decide no backtest e ao
vivo). Aqui ficam só as informações para o seletor escolher: em que mercado cada uma pode
operar, a expiração, o horário e se dá para validar com o histórico de velas.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from strategy import TATICAS


def id_estrategia(familia: str, params: dict) -> str:
    """Mesmo formato de main.carregar_estrategia (mantém o histórico do banco compatível)."""
    p = dict(params)
    p.setdefault("exp", 1)
    p.setdefault("lado", 1)
    return f"{familia}:{json.dumps(p, sort_keys=True)}"


@dataclass(frozen=True)
class Estrategia:
    chave: str                     # nome curto e fixo (usado no config e no app)
    nome: str                      # nome para pessoas
    familia: str                   # família em strategy.py
    params: dict = field(hash=False)
    mercados: tuple = ("op", "otc")          # onde pode operar
    validavel: bool = True                   # dá para testar com velas históricas?
    horas_brt: dict = field(default_factory=dict, hash=False)   # {"op": [9, 18]} = só nesse horário
    descricao: str = ""

    @property
    def exp(self) -> int:
        return int(self.params.get("exp", 1))

    @property
    def id(self) -> str:
        return id_estrategia(self.familia, self.params)

    def params_completos(self) -> dict:
        p = dict(self.params)
        p.setdefault("exp", 1)
        p.setdefault("lado", 1)
        return p

    def no_horario(self, mercado: str, hora_brt: int) -> bool:
        h = self.horas_brt.get(mercado)
        return h is None or int(h[0]) <= hora_brt < int(h[1])


_REV = {"k": 5, "regime": "todas", "exp": 5}
CATALOGO: list[Estrategia] = [
    Estrategia("reversao_5m", "Reversão 5 min", "regime", {**_REV, "z": 1.0, "lado": -1},
               horas_brt={"op": [9, 18]},
               descricao="Preço estica forte em 5 min → entra contra, expiração 5 min."),
    Estrategia("reversao_5m_forte", "Reversão 5 min (forte)", "regime", {**_REV, "z": 1.5, "lado": -1},
               horas_brt={"op": [9, 18]},
               descricao="Igual à reversão, mas só em esticões maiores (menos entradas)."),
    Estrategia("seguir_5m", "Seguir o movimento 5 min", "regime", {**_REV, "z": 1.5, "lado": 1},
               horas_brt={"op": [9, 18]},
               descricao="Preço estica forte em 5 min → entra a favor, expiração 5 min."),
    Estrategia("confluencia_5_m5", "Confluência 5+ sinais (M5)", "confluencia", {"n": 5, "exp": 5},
               mercados=("op",),
               descricao="Só entra quando 5 ou mais dos 7 sinais de reversão concordam e nenhum discorda; "
                         "expiração 5 min. Nos pares de mercado: 52,9% em 30 dias (o mais perto do empate)."),
    Estrategia("confluencia_4_m5", "Confluência 4+ sinais (M5)", "confluencia", {"n": 4, "exp": 5},
               mercados=("op",),
               descricao="Igual, com 4 ou mais sinais: mais entradas, um pouco menos de acerto (52,7%)."),
    Estrategia("humor_70", "Contra a multidão (humor 70%)", "humor", {"limite": 0.70, "exp": 1},
               mercados=("otc",), validavel=False,
               descricao="70% ou mais dos traders de um lado → entra do outro, expiração 1 min. "
                         "Só existe ao vivo (a IQ não guarda histórico do humor)."),
    Estrategia("reabertura_otc", "Reabertura do OTC (salto diário)", "reabertura", {"exp": 1, "z_min": 10},
               mercados=("otc",), validavel=False,
               descricao="Na volta da pausa diária de 5 min do OTC, entra contra o caminho que o preço andou "
                         "desde a reabertura anterior, expiração 1 min. Só tem vantagem se a compra sair no "
                         "preço antigo, no primeiro instante. Fora das janelas, não opera."),
] + [
    Estrategia(f"quadrante_{t}", f"Quadrante · {t.replace('_', ' ')}", "quadrante", {"tatica": t, "exp": 1},
               descricao="Tática de quadrante de 5 velas, expiração 1 min.")
    for t in TATICAS
]
POR_CHAVE = {e.chave: e for e in CATALOGO}


def obter(chave: str) -> Estrategia:
    if chave not in POR_CHAVE:
        raise KeyError(f"estratégia desconhecida: {chave}. Opções: {list(POR_CHAVE)}")
    return POR_CHAVE[chave]


def para_mercado(mercado: str) -> list[Estrategia]:
    return [e for e in CATALOGO if mercado in e.mercados]
