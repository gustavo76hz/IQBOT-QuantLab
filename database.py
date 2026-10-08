"""SQLite: operações, sinais bloqueados e eventos.

Cada operação abre e FECHA a conexão (no Windows o SQLite trava o arquivo enquanto
houver conexão aberta; assim dá para mover/arquivar o banco com o bot parado)."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ESQUEMA = """
CREATE TABLE IF NOT EXISTS operacoes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  aberta_em TEXT, ativo TEXT, direcao TEXT, valor REAL, exp INTEGER, payout REAL,
  estrategia TEXT, validada INTEGER, conta TEXT, gale INTEGER DEFAULT 0,
  order_id TEXT, resultado TEXT, lucro REAL, fechada_em TEXT, obs TEXT
);
CREATE TABLE IF NOT EXISTS sinais_bloqueados (
  id INTEGER PRIMARY KEY AUTOINCREMENT, em TEXT, ativo TEXT, direcao TEXT,
  estrategia TEXT, motivo TEXT
);
CREATE TABLE IF NOT EXISTS eventos (
  id INTEGER PRIMARY KEY AUTOINCREMENT, em TEXT, nivel TEXT, msg TEXT
);
CREATE TABLE IF NOT EXISTS trocas_estrategia (
  id INTEGER PRIMARY KEY AUTOINCREMENT, em TEXT, mercado TEXT, de TEXT, para TEXT,
  motivo TEXT, quem TEXT
);
"""


def agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class BancoDados:
    def __init__(self, caminho: str | Path):
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._con() as c:
            c.executescript(ESQUEMA)

    @contextmanager
    def _con(self):
        with self._lock:
            con = sqlite3.connect(self.caminho, timeout=10)
            try:
                yield con
                con.commit()
            finally:
                con.close()

    def abrir_operacao(self, ativo, direcao, valor, exp, payout, estrategia, validada,
                       conta, gale=0, aberta_em: str | None = None) -> int:
        with self._con() as c:
            cur = c.execute(
                "INSERT INTO operacoes (aberta_em, ativo, direcao, valor, exp, payout, estrategia,"
                " validada, conta, gale, resultado) VALUES (?,?,?,?,?,?,?,?,?,?, 'aberta')",
                (aberta_em or agora_iso(), ativo, direcao, valor, exp, payout, estrategia,
                 int(bool(validada)), conta, gale))
            return int(cur.lastrowid)

    def marcar_ordem(self, op_id: int, order_id, obs: str | None = None):
        with self._con() as c:
            c.execute("UPDATE operacoes SET order_id=?, obs=? WHERE id=?", (str(order_id), obs, op_id))

    def fechar_operacao(self, op_id: int, resultado: str, lucro: float, fechada_em: str | None = None):
        with self._con() as c:
            c.execute("UPDATE operacoes SET resultado=?, lucro=?, fechada_em=? WHERE id=?",
                      (resultado, float(lucro), fechada_em or agora_iso(), op_id))

    def bloquear_sinal(self, ativo, direcao, estrategia, motivo, em: str | None = None):
        with self._con() as c:
            c.execute("INSERT INTO sinais_bloqueados (em, ativo, direcao, estrategia, motivo) VALUES (?,?,?,?,?)",
                      (em or agora_iso(), ativo, direcao, estrategia, motivo))

    def evento(self, nivel: str, msg: str):
        with self._con() as c:
            c.execute("INSERT INTO eventos (em, nivel, msg) VALUES (?,?,?)", (agora_iso(), nivel, msg))

    def operacoes_df(self, conta: str | None = None, estrategia: str | None = None) -> pd.DataFrame:
        q = "SELECT * FROM operacoes WHERE 1=1"
        a: list = []
        if conta:
            q += " AND conta=?"
            a.append(conta)
        if estrategia:
            q += " AND estrategia=?"
            a.append(estrategia)
        with self._con() as c:
            return pd.read_sql_query(q + " ORDER BY id", c, params=a)

    def bloqueados_df(self) -> pd.DataFrame:
        with self._con() as c:
            return pd.read_sql_query("SELECT * FROM sinais_bloqueados ORDER BY id", c)

    def resumo_do_dia(self, conta: str, dia_br: str) -> dict:
        """Lucro, nº de operações e horários de abertura do dia (horário de Brasília)."""
        df = self.operacoes_df(conta)
        if not len(df):
            return {"dia": dia_br, "lucro": 0.0, "ops": 0, "aberturas": []}
        t = pd.to_datetime(df["aberta_em"], utc=True)
        df = df[((t - pd.Timedelta(hours=3)).dt.date.astype(str) == dia_br)  # Brasília = UTC-3
                & (df["resultado"] != "erro")]
        aberturas = [x.to_pydatetime() for x in pd.to_datetime(df["aberta_em"], utc=True)]
        return {"dia": dia_br, "lucro": float(df["lucro"].fillna(0).sum()), "ops": int(len(df)),
                "aberturas": aberturas}

    def registrar_troca(self, mercado: str, de: str, para: str, motivo: str, quem: str,
                        em: str | None = None) -> None:
        with self._con() as c:
            c.execute("INSERT INTO trocas_estrategia (em, mercado, de, para, motivo, quem) VALUES (?,?,?,?,?,?)",
                      (em or agora_iso(), mercado, de, para, motivo, quem))

    def trocas_df(self, limite: int = 50) -> pd.DataFrame:
        with self._con() as c:
            return pd.read_sql_query("SELECT * FROM trocas_estrategia ORDER BY id DESC LIMIT ?", c, params=(limite,))

    def lucro_total(self, conta: str) -> float:
        with self._con() as c:
            v = c.execute("SELECT COALESCE(SUM(lucro),0) FROM operacoes WHERE conta=? AND lucro IS NOT NULL",
                          (conta,)).fetchone()[0]
        return float(v)
