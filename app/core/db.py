"""
Conexão SQLite. Uma conexão por thread, WAL ligado, foreign keys ligadas.

Por que sqlite3 da stdlib e não um ORM: o núcleo de memória é o ativo mais
defensável do projeto e deve ter o mínimo de dependências possível. SQL puro
aqui é migrável para Postgres em um dia. Um ORM não é.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

_local = threading.local()


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._shared: sqlite3.Connection | None = None
        if path == ":memory:":  # testes: uma conexão só, senão cada thread vê um banco vazio
            self._shared = sqlite3.connect(path, check_same_thread=False)
            _configure(self._shared)

    def connect(self) -> sqlite3.Connection:
        if self._shared is not None:
            return self._shared
        conn = getattr(_local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, check_same_thread=False)
            _configure(conn)
            _local.conn = conn
        return conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Transação. Commit no sucesso, rollback em qualquer exceção.

        O código antigo não tinha transação nenhuma: uma ingestão que falhasse
        no meio deixava metade dos nós no grafo e nenhuma aresta.
        """
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def migrate(self) -> None:
        conn = self.connect()
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        # Colunas novas em bancos já existentes (CREATE IF NOT EXISTS não altera)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(finance_transactions)").fetchall()}
        if "to_account_id" not in cols:
            conn.execute("ALTER TABLE finance_transactions ADD COLUMN to_account_id TEXT")
        conn.commit()

    def close(self) -> None:
        if self._shared is not None:
            self._shared.close()
            self._shared = None
        conn = getattr(_local, "conn", None)
        if conn is not None:
            conn.close()
            _local.conn = None
