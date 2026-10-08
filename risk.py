"""Gestão de risco: entrada, stop/meta diária, limites, kill switch, orçamento total
e martingale (opcional, limitado, desligado por padrão)."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

FUSO_BR = timezone(timedelta(hours=-3))  # Brasília (sem horário de verão desde 2019)


def tabela_martingale(base: float, fator: float, gales: int, payout: float) -> str:
    """Mostra a conta do martingale ao usuário."""
    linhas = ["Martingale — a conta:"]
    acum = 0.0
    for g in range(gales + 1):
        v = base * fator ** g
        acum += v
        ganho = v * payout - (acum - v)
        linhas.append(f"  entrada {g} ({'inicial' if g == 0 else f'gale {g}'}): {v:.2f} | "
                      f"se ganhar aqui, lucro da sequência = {ganho:+.2f} | investido até aqui = {acum:.2f}")
    linhas.append(f"  Perder a sequência inteira custa {acum:.2f} (= {acum / base:.1f} entradas) "
                  f"para ganhar ~{base * payout:.2f} quando dá certo.")
    linhas.append("  Martingale NÃO cria vantagem: troca muitos ganhos pequenos por poucas perdas grandes.")
    return "\n".join(linhas)


class GestorRisco:
    def __init__(self, cfg: dict, saldo_inicial: float, prejuizo_acumulado: float = 0.0,
                 pasta_stop: str | Path = ".", payout_ref: float = 0.85,
                 hoje: dict | None = None):
        """hoje: resultado do dia já registrado no banco antes de (re)abrir o bot
        {"dia": "AAAA-MM-DD" (Brasília), "lucro": float, "ops": int, "aberturas": [datetime]}.
        Assim reiniciar o bot NÃO zera o stop diário nem os limites do dia."""
        self.cfg = cfg
        hoje = hoje or {}
        self.lucro_dia = float(hoje.get("lucro", 0.0))
        self.saldo_ini_dia = float(saldo_inicial) - self.lucro_dia  # saldo no começo do dia
        self.lucro_total = float(prejuizo_acumulado)  # desde o início (do banco)
        self.dia: str | None = hoje.get("dia")
        self.ops_hora: deque[datetime] = deque(sorted(hoje.get("aberturas", [])))
        self.ops_dia = int(hoje.get("ops", 0))
        self.perdas_seguidas = 0
        self.gale_atual = 0
        self.pausa_ate: datetime | None = None
        self.parado_motivo: str | None = None
        self.arquivo_stop = Path(pasta_stop) / "STOP"
        self.payout_ref = payout_ref
        self.abertas = 0
        self._resultados: dict[int, str] = {}  # op_id -> resultado (desta sessão)
        self.lucro_inicio_sessao = self.lucro_total   # para o limite de perda desta sessão

    # ------------------------------------------------------------------
    def _virar_dia(self, agora: datetime, saldo_atual: float | None):
        d = agora.astimezone(FUSO_BR).date().isoformat()
        if d != self.dia:
            self.dia = d
            self.lucro_dia = 0.0
            self.ops_dia = 0
            if saldo_atual is not None:
                self.saldo_ini_dia = float(saldo_atual)
            if self.parado_motivo and self.parado_motivo.startswith("dia:"):
                self.parado_motivo = None

    def pode_operar(self, agora: datetime, saldo_atual: float | None = None) -> tuple[bool, str]:
        self._virar_dia(agora, saldo_atual)
        r = self.cfg
        if self.arquivo_stop.exists():
            return False, "kill switch: arquivo STOP existe (apague-o para voltar)"
        if self.lucro_total <= -abs(r["orcamento_teste"]):
            self.parado_motivo = "orcamento"
            return False, f"orçamento de teste esgotado ({self.lucro_total:.2f})"
        if self.parado_motivo and not self.parado_motivo.startswith("dia:"):
            return False, self.parado_motivo
        # Limite de perda desta sessão (desde que o bot foi iniciado), em dinheiro. 0 = desligado.
        limite = float(r.get("perda_max_sessao") or 0)
        perda = self.lucro_inicio_sessao - self.lucro_total
        entrada = float(r["entrada"])
        if limite > 0 and perda + (self.abertas + 1) * entrada > limite + 1e-9:
            # nunca passa do limite: conta também o pior caso das operações ainda abertas
            if self.abertas == 0:
                self.parar(f"LIMITE DA SESSÃO: perdeu {perda:.2f} desde que o bot foi iniciado "
                           f"(mais uma entrada passaria de {limite:.2f}) — bot parado")
                return False, self.parado_motivo
            return False, f"limite da sessão: aguardando as operações abertas (perda {perda:.2f} de {limite:.2f})"
        max_seq = int(r["max_perdas_seguidas"] or 0)       # 0 = sem stop de perdas seguidas
        # Não deixa passar do stop de perdas seguidas: se já falta só 1 perda para o
        # stop e ainda há operação aberta, espera o resultado antes de abrir outra.
        # Desligável com risco.esperar_resultado_antes_do_stop: false (aí pode passar do stop).
        if (max_seq > 0 and r.get("parar_apos_perdas_seguidas") and r.get("esperar_resultado_antes_do_stop", True)
                and self.abertas > 0
                and max(self.perdas_seguidas, self._perdas_finais()) >= int(r["max_perdas_seguidas"]) - 1):
            return False, "aguardando resultado aberto (1 perda antes do stop)"
        # perda máxima do dia em dinheiro (0 = desligado); conta o pior caso das operações abertas
        valor_dia = float(r.get("stop_diario_valor") or 0)
        if valor_dia > 0 and -self.lucro_dia + (self.abertas + 1) * float(r["entrada"]) > valor_dia + 1e-9:
            if self.abertas == 0:
                self.parado_motivo = "dia:stop"
                return False, f"perda máxima do dia atingida ({self.lucro_dia:.2f} de -{valor_dia:.2f})"
            return False, f"perda do dia: aguardando as operações abertas ({self.lucro_dia:.2f} de -{valor_dia:.2f})"
        if self.lucro_dia <= -self.saldo_ini_dia * r["stop_diario_pct"] / 100:
            self.parado_motivo = "dia:stop"
            return False, f"stop diário atingido ({self.lucro_dia:.2f})"
        meta = float(r.get("meta_diaria_pct") or 0)  # 0 = sem meta (opera o dia todo)
        if meta > 0 and self.lucro_dia >= self.saldo_ini_dia * meta / 100 and self.gale_atual == 0:
            self.parado_motivo = "dia:meta"
            return False, f"meta diária atingida ({self.lucro_dia:+.2f})"
        if self.pausa_ate and agora < self.pausa_ate:
            return False, f"pausa após perdas seguidas até {self.pausa_ate.astimezone(FUSO_BR):%H:%M}"
        while self.ops_hora and agora - self.ops_hora[0] > timedelta(hours=1):
            self.ops_hora.popleft()
        if len(self.ops_hora) >= r["max_operacoes_hora"]:
            return False, "limite de operações por hora"
        if self.ops_dia >= r["max_operacoes_dia"]:
            return False, "limite de operações no dia"
        return True, "ok"

    def valor_entrada(self) -> float:
        r = self.cfg
        v = float(r["entrada"])
        m = r["martingale"]
        if m["ativo"] and self.gale_atual > 0:
            v = v * float(m["fator"]) ** self.gale_atual
        return round(min(v, float(r["max_entrada"])), 2)

    def registrar_abertura(self, agora: datetime):
        self.ops_hora.append(agora)
        self.ops_dia += 1
        self.abertas += 1

    def _sequencia_por_entrada(self) -> int:
        """Maior sequência de perdas na ORDEM DE ENTRADA (não na ordem em que os
        resultados chegam: com vários ativos ao mesmo tempo eles chegam embaralhados)."""
        maior = atual = 0
        for k in sorted(self._resultados):
            r = self._resultados[k]
            if r == "loose":
                atual += 1
                maior = max(maior, atual)
            elif r == "win":
                atual = 0
        return maior

    def _perdas_finais(self) -> int:
        """Perdas seguidas no FIM da sequência (ordem de entrada) entre as já fechadas."""
        n = 0
        for k in sorted(self._resultados, reverse=True):
            r = self._resultados[k]
            if r == "loose":
                n += 1
            elif r == "win":
                break
        return n

    def registrar_resultado(self, resultado: str, lucro: float, agora: datetime, op_id: int | None = None):
        self.abertas = max(0, self.abertas - 1)
        if op_id is not None:
            self._resultados[op_id] = resultado
        self.lucro_dia += lucro
        self.lucro_total += lucro
        m = self.cfg["martingale"]
        if resultado == "loose":
            self.perdas_seguidas += 1
            if m["ativo"] and self.gale_atual < int(m["max_gales"]):
                self.gale_atual += 1
            else:
                self.gale_atual = 0
            seq = max(self.perdas_seguidas, self._sequencia_por_entrada())
            max_seq = int(self.cfg["max_perdas_seguidas"] or 0)     # 0 = sem stop de perdas seguidas
            if max_seq <= 0:
                pass
            elif seq >= max_seq and self.cfg.get("parar_apos_perdas_seguidas"):
                self.parar(f"STOP LOSS: {seq} perdas seguidas — bot parado "
                           "(feche e abra de novo para recomeçar)")
            elif self.perdas_seguidas >= max_seq:
                self.pausa_ate = agora + timedelta(minutes=self.cfg["pausa_apos_perdas_min"])
                self.perdas_seguidas = 0
        elif resultado == "win":
            self.perdas_seguidas = 0
            self.gale_atual = 0

    def parar(self, motivo: str):
        self.parado_motivo = motivo

    def orcamento_restante(self) -> float:
        return abs(self.cfg["orcamento_teste"]) + self.lucro_total
