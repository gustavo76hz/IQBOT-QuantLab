"""TODA a dependência da iqoptionapi (7.1.1, não oficial) fica neste arquivo.

Particularidades tratadas (ver README, seção "Por que o código é assim"):
 1. get_all_open_time() trava no mercado digital -> usamos get_all_init_v2().
 2. Pares normais têm sufixo "-op"; OTC "-OTC".
 3. A lib NÃO registra os ids dos "-op" -> registramos em constants.ACTIVES.
 4. Payout vem da comissão: (100 - commission) / 100.
 5. Não dependemos da lista digital.
 6. Resultado: polling em api.api.socket_option_closed[id]; check_win_v3 só no fim.
 7. Toda chamada roda em thread com timeout (a lib tem loops infinitos).
 8. get_candles: só usamos velas FECHADAS; histórico paginado por endtime.
 9. connect() -> (ok, motivo); trata 2FA.
10. Reconexão com espera crescente.
"""
from __future__ import annotations

import logging
import os
import threading
import time

import pandas as pd

log = logging.getLogger("bot.conexao")


class TempoEsgotado(Exception):
    pass


def chamar(fn, *args, timeout: float = 30, **kw):
    """Roda fn em thread separada; se passar do timeout, levanta TempoEsgotado.
    (A thread presa fica como daemon e morre quando o programa fechar.)"""
    res: dict = {}

    def alvo():
        try:
            res["v"] = fn(*args, **kw)
        except BaseException as e:  # noqa: BLE001
            res["e"] = e

    t = threading.Thread(target=alvo, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TempoEsgotado(f"{getattr(fn, '__name__', fn)} passou de {timeout}s")
    if "e" in res:
        raise res["e"]
    return res.get("v")


def velas_para_df(velas: list[dict], tamanho_s: int, agora_servidor: float | None) -> pd.DataFrame:
    if not velas:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(velas)
    df = df.rename(columns={"min": "low", "max": "high"})
    df.index = pd.to_datetime(df["from"].astype("int64"), unit="s", utc=True)
    df.index.name = "time"
    if agora_servidor is not None:
        df = df[df["from"].astype(float) + tamanho_s <= agora_servidor]  # só fechadas
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    return df[~df.index.duplicated(keep="last")].sort_index()


class CorretoraIQ:
    """Implementa a interface de corretora usada pelo bot (ver fake_broker.py)."""

    def __init__(self, email: str | None = None, senha: str | None = None,
                 timeout: float = 30, max_tentativas: int = 8, pedir_2fa=input):
        self.email = email or os.getenv("IQ_EMAIL")
        self.senha = senha or os.getenv("IQ_PASSWORD")
        if not self.email or not self.senha:
            raise RuntimeError("Defina IQ_EMAIL e IQ_PASSWORD no arquivo .env")
        self.timeout = timeout
        self.max_tentativas = max_tentativas
        self.pedir_2fa = pedir_2fa
        self.api = None
        self.tipo_conta = "PRACTICE"
        self._ids: dict[str, int] = {}
        self._payout: dict[str, float] = {}
        # o avaliador baixa velas numa thread usando ESTA conexão (um 2º login derrubaria a sessão);
        # a trava evita que dois pedidos à lib aconteçam ao mesmo tempo
        self._trava_api = threading.RLock()

    # ------------------------------------------------------------ conexão
    def conectar(self, tipo_conta: str = "PRACTICE") -> None:
        from iqoptionapi.stable_api import IQ_Option

        logging.getLogger("websocket").setLevel(logging.CRITICAL)
        self.tipo_conta = tipo_conta
        espera = 5
        for tentativa in range(1, self.max_tentativas + 1):
            try:
                self.api = IQ_Option(self.email, self.senha)
                ok, motivo = chamar(self.api.connect, timeout=self.timeout)
                if not ok and motivo == "2FA":
                    codigo = self.pedir_2fa("Código 2FA recebido por SMS/e-mail: ").strip()
                    ok, motivo = chamar(self.api.connect_2fa, codigo, timeout=self.timeout)
                if ok:
                    chamar(self.api.change_balance, tipo_conta, timeout=self.timeout)
                    modo = chamar(self.api.get_balance_mode, timeout=self.timeout)
                    if modo != tipo_conta:
                        raise RuntimeError(f"pedi conta {tipo_conta} mas a corretora está em {modo}")
                    log.info("conectado (%s)", tipo_conta)
                    self._ids.clear()
                    return
                if motivo and ("invalid_credentials" in str(motivo) or "wrong" in str(motivo).lower()):
                    raise RuntimeError(f"login recusado: {motivo}")
                log.warning("falha ao conectar (%s), tentativa %d", motivo, tentativa)
            except (TempoEsgotado, OSError, ValueError, KeyError) as e:
                log.warning("erro ao conectar: %s (tentativa %d)", e, tentativa)
            time.sleep(espera)
            espera = min(espera * 2, 300)  # espera crescente: evita bloqueio de IP
        raise RuntimeError("não foi possível conectar. Feche o app/outros scripts na mesma conta e tente depois.")

    def conectado(self) -> bool:
        try:
            return bool(self.api and self.api.check_connect())
        except Exception:
            return False

    def garantir_conexao(self) -> None:
        if not self.conectado():
            log.warning("conexão caiu — reconectando")
            self.conectar(self.tipo_conta)

    # ------------------------------------------------------------ ativos
    def ativos_abertos(self) -> dict[str, float]:
        """{nome: payout} dos ativos turbo abertos agora. Também registra os ids."""
        from iqoptionapi import constants

        self.garantir_conexao()
        with self._trava_api:
            data = chamar(self.api.get_all_init_v2, timeout=self.timeout + 5)
        if not data or "turbo" not in data:
            raise RuntimeError("get_all_init_v2 não respondeu")
        abertos = {}
        for id_, a in data["turbo"]["actives"].items():
            nome = str(a.get("name", "")).split(".", 1)[-1]
            if not nome:
                continue
            constants.ACTIVES.setdefault(nome, int(id_))
            self._ids[nome] = int(id_)
            try:
                payout = (100 - float(a["option"]["profit"]["commission"])) / 100
            except (KeyError, TypeError, ValueError):
                payout = self._payout.get(nome, 0.0)
            self._payout[nome] = payout
            if a.get("enabled") and not a.get("is_suspended"):
                abertos[nome] = payout
        return abertos

    def payout(self, ativo: str) -> float:
        if ativo not in self._payout:
            self.ativos_abertos()
        return self._payout.get(ativo, 0.0)

    def _registrar(self, ativo: str) -> None:
        from iqoptionapi import constants

        if ativo not in constants.ACTIVES:
            self.ativos_abertos()
        if ativo not in constants.ACTIVES:
            raise KeyError(f"ativo {ativo} não existe na lista turbo da IQ")

    # ------------------------------------------------------------ dados
    def hora_servidor(self) -> float:
        try:
            ts = float(self.api.get_server_timestamp() or 0)
            if ts > 1e11:  # veio em milissegundos
                ts /= 1000
            return ts if abs(ts - time.time()) < 3600 else time.time()
        except Exception:
            return time.time()

    def velas(self, ativo: str, tamanho_s: int = 60, quantidade: int = 1000,
              ate: float | None = None) -> pd.DataFrame:
        """Velas FECHADAS. Para mais de 1000, pagina para trás por endtime."""
        self.garantir_conexao()
        self._registrar(ativo)
        agora = self.hora_servidor()
        fim = ate or agora
        partes = []
        faltam = quantidade + 1
        while faltam > 0:
            n = min(1000, faltam)
            with self._trava_api:
                v = chamar(self.api.get_candles, ativo, tamanho_s, n, fim, timeout=self.timeout)
            if not v:
                break
            partes.append(velas_para_df(v, tamanho_s, agora))
            faltam -= len(v)
            fim = min(x["from"] for x in v) - 1
            if len(v) < n:
                break
        if not partes:
            return velas_para_df([], tamanho_s, agora)
        df = pd.concat(partes).sort_index()
        return df[~df.index.duplicated(keep="last")].tail(quantidade)

    # ------------------------------------------------------------ humor dos traders
    def humor(self, ativo: str) -> float | None:
        """Fração dos traders em CALL (0..1) neste ativo, ou None se ainda não chegou.
        Assina o canal 'traders-mood-changed' uma vez por ativo (sem o loop infinito da lib)."""
        from iqoptionapi import constants
        try:
            self._registrar(ativo)
            aid = constants.ACTIVES[ativo]
            if not hasattr(self, "_humor_assinado"):
                self._humor_assinado = set()
            if aid not in self._humor_assinado:
                self.api.api.subscribe_Traders_mood(aid, "turbo-option")
                self._humor_assinado.add(aid)
            v = self.api.api.traders_mood.get(aid)
            return None if v is None else float(v)
        except Exception as e:  # noqa: BLE001
            log.debug("humor de %s indisponível: %s", ativo, e)
            return None

    # ------------------------------------------------------------ ordens
    def comprar(self, ativo: str, valor: float, direcao: str, exp_min: int):
        """Retorna (ok, id_ordem_ou_mensagem)."""
        self.garantir_conexao()
        self._registrar(ativo)
        ok, ident = chamar(self.api.buy, float(valor), ativo, direcao.lower(), int(exp_min),
                           timeout=15)
        return bool(ok), ident

    def resultado(self, id_ordem, timeout: float) -> tuple[str, float]:
        """Espera o fechamento. Retorna (win|loose|equal|desconhecido, lucro)."""
        fim = time.time() + timeout
        while time.time() < fim:
            try:
                x = self.api.api.socket_option_closed.get(id_ordem)
            except Exception:
                x = None
            if x and "msg" in x:
                m = x["msg"]
                r = m.get("win")
                soma = float(m.get("sum", 0) or 0)
                if r == "win":
                    return "win", float(m.get("win_amount", 0) or 0) - soma
                if r == "loose":
                    return "loose", -soma
                if r == "equal":
                    return "equal", 0.0
            time.sleep(0.5)
        try:  # último recurso (bloqueia; por isso em thread com timeout)
            r, lucro = chamar(self.api.check_win_v3, id_ordem, timeout=60)
            return str(r), float(lucro)
        except Exception as e:
            log.error("não consegui o resultado da ordem %s: %s", id_ordem, e)
            return "desconhecido", 0.0

    # ------------------------------------------------------------ conta
    def saldo(self) -> float:
        self.garantir_conexao()
        return float(chamar(self.api.get_balance, timeout=self.timeout))

    def moeda(self) -> str:
        try:
            return str(chamar(self.api.get_currency, timeout=self.timeout))
        except Exception:
            return "?"
