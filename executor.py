"""Envia ordens, registra no banco e acompanha o resultado em segundo plano."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

log = logging.getLogger("bot.executor")


class Executor:
    def __init__(self, corretora, banco, risco, notificador, conta: str, relogio=None):
        self.c = corretora
        self.db = banco
        self.risco = risco
        self.notif = notificador
        self.conta = conta
        self.relogio = relogio
        self.ocupados: set[str] = set()
        self.ocupado_ate: dict[str, float] = {}  # sem sobreposição: igual ao backtest
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self.ultimas: list[dict] = []  # para o painel

    def _agora(self) -> datetime:
        if self.relogio:
            return self.relogio.agora_dt()
        return datetime.now(timezone.utc)

    def livre(self, ativo: str, agora_ts: float) -> bool:
        return ativo not in self.ocupados and agora_ts >= self.ocupado_ate.get(ativo, 0)

    def enviar(self, ativo: str, direcao: int, exp: int, payout: float,
               estrategia: str, validada: bool, registrar_recusa: bool = True,
               obs: str | None = None) -> int | None:
        """registrar_recusa=False: compra recusada não vira linha 'erro' no banco (usado pela
        reabertura, que tenta várias vezes por segundo até o par reabrir)."""
        agora = self._agora()
        if not self.livre(ativo, agora.timestamp()):
            return None
        lado = "call" if direcao > 0 else "put"
        valor = self.risco.valor_entrada()
        gale = self.risco.gale_atual
        op = None
        if registrar_recusa:
            op = self.db.abrir_operacao(ativo, lado, valor, exp, payout, estrategia, validada,
                                        self.conta, gale, aberta_em=agora.isoformat(timespec="seconds"))
        try:
            ok, ident = self.c.comprar(ativo, valor, lado, exp)
        except Exception as e:  # noqa: BLE001
            ok, ident = False, str(e)
        if not ok:
            if op is not None:
                self.db.fechar_operacao(op, "erro", 0.0)
                self.db.marcar_ordem(op, "", obs=f"compra recusada: {ident}")
                log.error("compra recusada %s %s: %s", ativo, lado, ident)
            else:
                log.debug("compra recusada %s %s: %s", ativo, lado, ident)
            return None
        if op is None:
            agora = self._agora()
            op = self.db.abrir_operacao(ativo, lado, valor, exp, payout, estrategia, validada,
                                        self.conta, gale, aberta_em=agora.isoformat(timespec="seconds"))
        self.db.marcar_ordem(op, ident, obs=obs)
        with self._lock:
            self.risco.registrar_abertura(agora)
            self.ocupados.add(ativo)
            self.ocupado_ate[ativo] = (agora.timestamp() // 60) * 60 + exp * 60
        reg = {"op": op, "ativo": ativo, "lado": lado, "valor": valor, "exp": exp,
               "hora": agora, "resultado": "aberta", "lucro": None, "gale": gale}
        self.ultimas.append(reg)
        self.ultimas[:] = self.ultimas[-200:]
        self.notif.enviar(f"{'REAL' if self.conta == 'REAL' else 'DEMO'}: {lado.upper()} {ativo} "
                          f"{valor:.2f} exp {exp}min (gale {gale})")
        if getattr(self.c, "sincrono", False):  # corretora falsa com relógio acelerado
            self._esperar(op, ident, exp, ativo, reg)
            return op
        t = threading.Thread(target=self._esperar, args=(op, ident, exp, ativo, reg), daemon=True)
        t.start()
        self._threads.append(t)
        return op

    def _esperar(self, op, ident, exp, ativo, reg):
        try:
            resultado, lucro = self.c.resultado(ident, timeout=exp * 60 + 90)
        except Exception as e:  # noqa: BLE001
            resultado, lucro = "desconhecido", 0.0
            log.error("erro ao buscar resultado: %s", e)
        self.db.fechar_operacao(op, resultado, lucro, fechada_em=self._agora().isoformat(timespec="seconds"))
        with self._lock:
            self.risco.registrar_resultado(resultado, lucro, self._agora(), op_id=op)
            self.ocupados.discard(ativo)
        reg["resultado"], reg["lucro"] = resultado, lucro
        self.notif.enviar(f"{ativo}: {resultado} {lucro:+.2f}")

    def aguardar_todas(self, timeout: float = 600):
        for t in list(self._threads):
            t.join(timeout)
        self._threads = [t for t in self._threads if t.is_alive()]
