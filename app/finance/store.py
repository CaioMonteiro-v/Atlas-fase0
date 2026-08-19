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
    FinanceBudgetCap,
    FinanceDebt,
    FinanceGoal,
    FinanceHealth,
    FinanceSnapshot,
    FinanceTransaction,
    Privacy,
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
        if tx.kind == "transferencia":
            if not tx.to_account_id:
                raise ValueError("transferência exige to_account_id")
            if tx.to_account_id == tx.account_id:
                raise ValueError("contas de origem e destino devem ser diferentes")
            return self._transfer(tx)

        delta = tx.amount if tx.kind == "receita" else -tx.amount
        with self.db.tx() as c:
            own = c.execute(
                "SELECT id FROM finance_accounts WHERE id = ? AND user_id = ?",
                (tx.account_id, tx.user_id),
            ).fetchone()
            if not own:
                raise ValueError("conta não encontrada")

            c.execute(
                """INSERT INTO finance_transactions
                   (id, user_id, account_id, to_account_id, kind, amount, category, description,
                    occurred_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tx.id, tx.user_id, tx.account_id, tx.to_account_id, tx.kind, tx.amount,
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

    def _transfer(self, tx: FinanceTransaction) -> FinanceTransaction:
        with self.db.tx() as c:
            src = c.execute(
                "SELECT id FROM finance_accounts WHERE id = ? AND user_id = ?",
                (tx.account_id, tx.user_id),
            ).fetchone()
            dst = c.execute(
                "SELECT id FROM finance_accounts WHERE id = ? AND user_id = ?",
                (tx.to_account_id, tx.user_id),
            ).fetchone()
            if not src or not dst:
                raise ValueError("conta de origem ou destino não encontrada")

            c.execute(
                """INSERT INTO finance_transactions
                   (id, user_id, account_id, to_account_id, kind, amount, category, description,
                    occurred_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tx.id, tx.user_id, tx.account_id, tx.to_account_id, "transferencia",
                    tx.amount, tx.category or "transferencia", tx.description,
                    _iso(tx.occurred_at), _iso(tx.created_at),
                ),
            )
            now = _iso(utcnow())
            c.execute(
                """UPDATE finance_accounts SET balance = balance - ?, updated_at = ?
                   WHERE id = ? AND user_id = ?""",
                (tx.amount, now, tx.account_id, tx.user_id),
            )
            c.execute(
                """UPDATE finance_accounts SET balance = balance + ?, updated_at = ?
                   WHERE id = ? AND user_id = ?""",
                (tx.amount, now, tx.to_account_id, tx.user_id),
            )
        return tx

    def delete_transaction(self, user_id: str, tx_id: str) -> bool:
        """Remove lançamento e reverte o saldo (melhor esforço)."""
        with self.db.tx() as c:
            r = c.execute(
                "SELECT * FROM finance_transactions WHERE id = ? AND user_id = ?",
                (tx_id, user_id),
            ).fetchone()
            if not r:
                return False
            kind, amount = r["kind"], float(r["amount"])
            account_id = r["account_id"]
            to_id = r["to_account_id"] if "to_account_id" in r.keys() else None
            now = _iso(utcnow())
            if kind == "transferencia" and to_id:
                c.execute(
                    "UPDATE finance_accounts SET balance = balance + ?, updated_at = ? WHERE id = ? AND user_id = ?",
                    (amount, now, account_id, user_id),
                )
                c.execute(
                    "UPDATE finance_accounts SET balance = balance - ?, updated_at = ? WHERE id = ? AND user_id = ?",
                    (amount, now, to_id, user_id),
                )
            else:
                delta = -amount if kind == "receita" else amount
                c.execute(
                    "UPDATE finance_accounts SET balance = balance + ?, updated_at = ? WHERE id = ? AND user_id = ?",
                    (delta, now, account_id, user_id),
                )
            c.execute("DELETE FROM finance_transactions WHERE id = ? AND user_id = ?", (tx_id, user_id))
        return True

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

    def monthly_report(self, user_id: str, month: str | None = None) -> dict[str, Any]:
        now = utcnow()
        if not month:
            month = f"{now.year:04d}-{now.month:02d}"
        year, mon = map(int, month.split("-"))
        start = datetime(year, mon, 1, tzinfo=timezone.utc)
        if mon == 12:
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(year, mon + 1, 1, tzinfo=timezone.utc)

        txs = [
            t for t in self.list_transactions(user_id, limit=5000, since=start)
            if t.occurred_at and t.occurred_at < end
        ]
        receita = sum(t.amount for t in txs if t.kind == "receita")
        despesa = sum(t.amount for t in txs if t.kind == "despesa")
        by_cat: dict[str, float] = {}
        for t in txs:
            if t.kind != "despesa":
                continue
            by_cat[t.category or "geral"] = by_cat.get(t.category or "geral", 0.0) + t.amount
        por_categoria = [
            {"categoria": k, "total": round(v, 2), "pct": round(100 * v / despesa, 1) if despesa else 0.0}
            for k, v in sorted(by_cat.items(), key=lambda x: -x[1])
        ]
        return {
            "month": month,
            "receita": round(receita, 2),
            "despesa": round(despesa, 2),
            "poupanca": round(receita - despesa, 2),
            "por_categoria": por_categoria,
            "lancamentos": len(txs),
        }

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

    # ----------------------------------------------------------------- dívidas
    def create_debt(self, debt: FinanceDebt) -> FinanceDebt:
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO finance_debts
                   (id, user_id, name, kind, balance, interest_rate_month, installment,
                    due_day, lender, notes, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    debt.id, debt.user_id, debt.name, debt.kind, debt.balance,
                    debt.interest_rate_month, debt.installment, debt.due_day,
                    debt.lender, debt.notes, debt.status,
                    _iso(debt.created_at), _iso(debt.updated_at),
                ),
            )
        return debt

    def list_debts(self, user_id: str, status: str | None = "ativa") -> list[FinanceDebt]:
        sql = "SELECT * FROM finance_debts WHERE user_id = ?"
        params: list[Any] = [user_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY interest_rate_month DESC, balance ASC"
        rows = self.db.connect().execute(sql, params).fetchall()
        return [self._to_debt(r) for r in rows]

    def get_debt(self, user_id: str, debt_id: str) -> FinanceDebt | None:
        r = self.db.connect().execute(
            "SELECT * FROM finance_debts WHERE id = ? AND user_id = ?",
            (debt_id, user_id),
        ).fetchone()
        return self._to_debt(r) if r else None

    def update_debt(self, user_id: str, debt_id: str, patch: dict[str, Any]) -> FinanceDebt | None:
        allowed = {
            "name", "balance", "interest_rate_month", "installment",
            "due_day", "lender", "notes", "status", "kind",
        }
        fields = {k: v for k, v in patch.items() if k in allowed and v is not None}
        if not fields:
            return self.get_debt(user_id, debt_id)
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [_iso(utcnow()), debt_id, user_id]
        with self.db.tx() as c:
            cur = c.execute(
                f"UPDATE finance_debts SET {sets}, updated_at = ? WHERE id = ? AND user_id = ?",
                values,
            )
            if cur.rowcount == 0:
                return None
        return self.get_debt(user_id, debt_id)

    def delete_debt(self, user_id: str, debt_id: str) -> bool:
        with self.db.tx() as c:
            cur = c.execute(
                "DELETE FROM finance_debts WHERE id = ? AND user_id = ?",
                (debt_id, user_id),
            )
            return cur.rowcount > 0

    # --------------------------------------------------------------- orçamento
    @staticmethod
    def current_month() -> str:
        now = utcnow()
        return f"{now.year:04d}-{now.month:02d}"

    def upsert_budget_cap(self, cap: FinanceBudgetCap) -> FinanceBudgetCap:
        existing = self.db.connect().execute(
            """SELECT id FROM finance_budget_caps
               WHERE user_id = ? AND month = ? AND category = ?""",
            (cap.user_id, cap.month, cap.category),
        ).fetchone()
        now = utcnow()
        if existing:
            with self.db.tx() as c:
                c.execute(
                    """UPDATE finance_budget_caps
                       SET limit_amount = ?, updated_at = ? WHERE id = ? AND user_id = ?""",
                    (cap.limit_amount, _iso(now), existing["id"], cap.user_id),
                )
            cap.id = existing["id"]
            cap.updated_at = now
            return cap
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO finance_budget_caps
                   (id, user_id, month, category, limit_amount, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    cap.id, cap.user_id, cap.month, cap.category, cap.limit_amount,
                    _iso(cap.created_at), _iso(cap.updated_at),
                ),
            )
        return cap

    def list_budget_caps(self, user_id: str, month: str | None = None) -> list[FinanceBudgetCap]:
        month = month or self.current_month()
        rows = self.db.connect().execute(
            """SELECT * FROM finance_budget_caps
               WHERE user_id = ? AND month = ? ORDER BY category""",
            (user_id, month),
        ).fetchall()
        return [self._to_budget(r) for r in rows]

    def delete_budget_cap(self, user_id: str, cap_id: str) -> bool:
        with self.db.tx() as c:
            cur = c.execute(
                "DELETE FROM finance_budget_caps WHERE id = ? AND user_id = ?",
                (cap_id, user_id),
            )
            return cur.rowcount > 0

    def budget_status(self, user_id: str, month: str | None = None) -> dict[str, Any]:
        month = month or self.current_month()
        report = self.monthly_report(user_id, month=month)
        spent_by = {c["categoria"]: c["total"] for c in report["por_categoria"]}
        caps = self.list_budget_caps(user_id, month)
        rows = []
        estouradas = 0
        total_limit = 0.0
        total_spent_capped = 0.0
        for cap in caps:
            spent = float(spent_by.get(cap.category, 0.0))
            pct = round(100 * spent / cap.limit_amount, 1) if cap.limit_amount > 0 else 0.0
            over = spent > cap.limit_amount
            if over:
                estouradas += 1
            total_limit += cap.limit_amount
            total_spent_capped += spent
            rows.append({
                "id": cap.id,
                "categoria": cap.category,
                "limite": round(cap.limit_amount, 2),
                "gasto": round(spent, 2),
                "restante": round(cap.limit_amount - spent, 2),
                "pct": pct,
                "estourada": over,
            })
        # categorias sem teto mas com gasto
        capped = {c.category for c in caps}
        for cat, total in spent_by.items():
            if cat not in capped:
                rows.append({
                    "id": None,
                    "categoria": cat,
                    "limite": None,
                    "gasto": round(total, 2),
                    "restante": None,
                    "pct": None,
                    "estourada": False,
                    "sem_teto": True,
                })
        return {
            "month": month,
            "caps": rows,
            "total_limit": round(total_limit, 2),
            "total_spent": round(total_spent_capped, 2),
            "estouradas": estouradas,
        }

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

        since_90 = now - timedelta(days=90)
        recent = self.list_transactions(user_id, limit=5000, since=since_90)
        despesa_90 = sum(t.amount for t in recent if t.kind == "despesa")
        despesa_mes_media = despesa_90 / 3.0 if despesa_90 > 0 else despesa
        reserva = (patrimonio / despesa_mes_media) if despesa_mes_media > 0 else None

        goals = self.list_goals(user_id, status="ativa")
        progresso = (
            round(sum(g.progress for g in goals) / len(goals), 2) if goals else 0.0
        )
        debts = self.list_debts(user_id, status="ativa")
        budget = self.budget_status(user_id)

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
            dividas_total=round(sum(d.balance for d in debts), 2),
            parcelas_mes=round(sum(d.installment for d in debts), 2),
            categorias_estouradas=int(budget["estouradas"]),
        )

    def snapshot(self, user_id: str) -> FinanceSnapshot:
        return FinanceSnapshot(
            health=self.health(user_id),
            contas=self.list_accounts(user_id),
            metas=self.list_goals(user_id, status="ativa"),
            recentes=self.list_transactions(user_id, limit=8),
            dividas=self.list_debts(user_id, status="ativa"),
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
        keys = r.keys()
        return FinanceTransaction(
            id=r["id"], user_id=r["user_id"], account_id=r["account_id"],
            to_account_id=r["to_account_id"] if "to_account_id" in keys else None,
            kind=r["kind"],
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

    @staticmethod
    def _to_debt(r: sqlite3.Row) -> FinanceDebt:
        return FinanceDebt(
            id=r["id"], user_id=r["user_id"], name=r["name"], kind=r["kind"],
            balance=float(r["balance"]),
            interest_rate_month=float(r["interest_rate_month"]),
            installment=float(r["installment"]),
            due_day=int(r["due_day"]), lender=r["lender"] or "", notes=r["notes"] or "",
            status=r["status"],
            created_at=_dt(r["created_at"]) or utcnow(),
            updated_at=_dt(r["updated_at"]) or utcnow(),
        )

    @staticmethod
    def _to_budget(r: sqlite3.Row) -> FinanceBudgetCap:
        return FinanceBudgetCap(
            id=r["id"], user_id=r["user_id"], month=r["month"], category=r["category"],
            limit_amount=float(r["limit_amount"]),
            created_at=_dt(r["created_at"]) or utcnow(),
            updated_at=_dt(r["updated_at"]) or utcnow(),
        )
