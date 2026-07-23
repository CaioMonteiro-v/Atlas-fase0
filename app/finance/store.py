"""
Persistência do Domínio Financeiro.

Mesma regra das outras camadas: TODA query filtra por user_id.
Saldos são atualizados na mesma transação do lançamento — nunca em dois passos.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.db import Database
from app.domain.models import (
    FinanceAccount,
    FinanceGoal,
    FinanceHealth,
    FinanceSnapshot,
    FinanceTransaction,
    Privacy,
    new_id,
    utcnow,
)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class FinanceStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------ contas
    def create_account(self, account: FinanceAccount) -> FinanceAccount:
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO finance_accounts
                   (id, user_id, name, kind, currency, balance, privacy, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    account.id, account.user_id, account.name, account.kind,
                    account.currency, account.balance, account.privacy.value,
                    _iso(account.created_at), _iso(account.updated_at),
                ),
            )
        return account

    def list_accounts(self, user_id: str) -> list[FinanceAccount]:
        rows = self.db.connect().execute(
            "SELECT * FROM finance_accounts WHERE user_id = ? ORDER BY name",
            (user_id,),
        ).fetchall()
        return [self._to_account(r) for r in rows]

    def get_account(self, user_id: str, account_id: str) -> FinanceAccount | None:
        r = self.db.connect().execute(
            "SELECT * FROM finance_accounts WHERE id = ? AND user_id = ?",
            (account_id, user_id),
        ).fetchone()
        return self._to_account(r) if r else None

    def delete_account(self, user_id: str, account_id: str) -> bool:
        with self.db.tx() as c:
            cur = c.execute(
                "DELETE FROM finance_accounts WHERE id = ? AND user_id = ?",
                (account_id, user_id),
            )
            return cur.rowcount > 0

    # ------------------------------------------------------------- lançamentos
    def add_transaction(self, tx: FinanceTransaction) -> FinanceTransaction:
        delta = tx.amount if tx.kind == "receita" else -tx.amount
        if tx.kind == "transferencia":
            delta = -tx.amount  # saída da conta origem; destino entra por outro lançamento

        with self.db.tx() as c:
            own = c.execute(
                "SELECT id FROM finance_accounts WHERE id = ? AND user_id = ?",
                (tx.account_id, tx.user_id),
            ).fetchone()
            if not own:
                raise ValueError("conta não encontrada")

            c.execute(
                """INSERT INTO finance_transactions
                   (id, user_id, account_id, kind, amount, category, description,
                    occurred_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tx.id, tx.user_id, tx.account_id, tx.kind, tx.amount,
                    tx.category, tx.description, _iso(tx.occurred_at), _iso(tx.created_at),
                ),
            )
            c.execute(
                """UPDATE finance_accounts
                   SET balance = balance + ?, updated_at = ?
                   WHERE id = ? AND user_id = ?""",
                (delta, _iso(utcnow()), tx.account_id, tx.user_id),
            )
        return tx

    def list_transactions(
        self,
        user_id: str,
        *,
        account_id: str | None = None,
        limit: int = 50,
        since: datetime | None = None,
    ) -> list[FinanceTransaction]:
        sql = "SELECT * FROM finance_transactions WHERE user_id = ?"
        params: list[Any] = [user_id]
        if account_id:
            sql += " AND account_id = ?"
            params.append(account_id)
        if since:
            sql += " AND occurred_at >= ?"
            params.append(_iso(since))
        sql += " ORDER BY occurred_at DESC LIMIT ?"
        params.append(limit)
        rows = self.db.connect().execute(sql, params).fetchall()
        return [self._to_tx(r) for r in rows]

    # ------------------------------------------------------------------- metas
    def create_goal(self, goal: FinanceGoal) -> FinanceGoal:
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO finance_goals
                   (id, user_id, journey_id, title, target_amount, current_amount,
                    deadline, status, privacy, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    goal.id, goal.user_id, goal.journey_id, goal.title,
                    goal.target_amount, goal.current_amount, _iso(goal.deadline),
                    goal.status, goal.privacy.value,
                    _iso(goal.created_at), _iso(goal.updated_at),
                ),
            )
        return goal

    def list_goals(self, user_id: str, status: str | None = None) -> list[FinanceGoal]:
        sql = "SELECT * FROM finance_goals WHERE user_id = ?"
        params: list[Any] = [user_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY updated_at DESC"
        rows = self.db.connect().execute(sql, params).fetchall()
        return [self._to_goal(r) for r in rows]

    def update_goal_progress(self, user_id: str, goal_id: str, current_amount: float) -> FinanceGoal | None:
        with self.db.tx() as c:
            cur = c.execute(
                """UPDATE finance_goals
                   SET current_amount = ?, updated_at = ?,
                       status = CASE WHEN ? >= target_amount THEN 'concluida' ELSE status END
                   WHERE id = ? AND user_id = ?""",
                (current_amount, _iso(utcnow()), current_amount, goal_id, user_id),
            )
            if cur.rowcount == 0:
                return None
        rows = self.list_goals(user_id)
        return next((g for g in rows if g.id == goal_id), None)

    def delete_goal(self, user_id: str, goal_id: str) -> bool:
        with self.db.tx() as c:
            cur = c.execute(
                "DELETE FROM finance_goals WHERE id = ? AND user_id = ?",
                (goal_id, user_id),
            )
            return cur.rowcount > 0

    # -------------------------------------------------------------- saúde / ctx
    def health(self, user_id: str) -> FinanceHealth:
        accounts = self.list_accounts(user_id)
        patrimonio = sum(a.balance for a in accounts)

        now = utcnow()
        month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        month_txs = self.list_transactions(user_id, limit=5000, since=month_start)
        receita = sum(t.amount for t in month_txs if t.kind == "receita")
        despesa = sum(t.amount for t in month_txs if t.kind == "despesa")
        poupanca = receita - despesa
        taxa = (poupanca / receita) if receita > 0 else 0.0
        comprometimento = (despesa / receita) if receita > 0 else 0.0

        # reserva: patrimônio / despesa média dos últimos 90 dias (anualizada em meses)
        since_90 = now - timedelta(days=90)
        recent = self.list_transactions(user_id, limit=5000, since=since_90)
        despesa_90 = sum(t.amount for t in recent if t.kind == "despesa")
        despesa_mes_media = despesa_90 / 3.0 if despesa_90 > 0 else despesa
        reserva = (patrimonio / despesa_mes_media) if despesa_mes_media > 0 else None

        goals = self.list_goals(user_id, status="ativa")
        progresso = (
            round(sum(g.progress for g in goals) / len(goals), 2) if goals else 0.0
        )

        return FinanceHealth(
            patrimonio=round(patrimonio, 2),
            receita_mes=round(receita, 2),
            despesa_mes=round(despesa, 2),
            poupanca_mes=round(poupanca, 2),
            taxa_poupanca=round(taxa, 3),
            comprometimento=round(comprometimento, 3),
            reserva_meses=round(reserva, 1) if reserva is not None else None,
            metas_ativas=len(goals),
            progresso_metas=progresso,
        )

    def snapshot(self, user_id: str) -> FinanceSnapshot:
        return FinanceSnapshot(
            health=self.health(user_id),
            contas=self.list_accounts(user_id),
            metas=self.list_goals(user_id, status="ativa"),
            recentes=self.list_transactions(user_id, limit=8),
        )

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _to_account(r: sqlite3.Row) -> FinanceAccount:
        return FinanceAccount(
            id=r["id"], user_id=r["user_id"], name=r["name"], kind=r["kind"],
            currency=r["currency"], balance=r["balance"], privacy=Privacy(r["privacy"]),
            created_at=_dt(r["created_at"]), updated_at=_dt(r["updated_at"]),
        )

    @staticmethod
    def _to_tx(r: sqlite3.Row) -> FinanceTransaction:
        return FinanceTransaction(
            id=r["id"], user_id=r["user_id"], account_id=r["account_id"], kind=r["kind"],
            amount=r["amount"], category=r["category"], description=r["description"],
            occurred_at=_dt(r["occurred_at"]), created_at=_dt(r["created_at"]),
        )

    @staticmethod
    def _to_goal(r: sqlite3.Row) -> FinanceGoal:
        return FinanceGoal(
            id=r["id"], user_id=r["user_id"], journey_id=r["journey_id"], title=r["title"],
            target_amount=r["target_amount"], current_amount=r["current_amount"],
            deadline=_dt(r["deadline"]), status=r["status"], privacy=Privacy(r["privacy"]),
            created_at=_dt(r["created_at"]), updated_at=_dt(r["updated_at"]),
        )
