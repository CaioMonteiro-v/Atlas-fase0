"""API do Domínio Financeiro (Cap. 82–92)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import get_memory
from app.core.security import current_user_id
from app.domain.models import (
    FinanceAccount,
    FinanceAccountCreate,
    FinanceGoal,
    FinanceGoalCreate,
    FinanceTransaction,
    FinanceTransactionCreate,
    utcnow,
)
from app.memory.service import MemoryService

router = APIRouter(prefix="/finance", tags=["financas"])


@router.get("/health")
def financial_health(
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    """Cap. 92 — indicadores de saúde financeira."""
    return memory.finance.health(user_id).model_dump(mode="json")


@router.get("/snapshot")
def snapshot(
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    snap = memory.finance.snapshot(user_id)
    data = snap.model_dump(mode="json")
    data["metas"] = [
        {**g.model_dump(mode="json"), "progress": g.progress} for g in snap.metas
    ]
    return data


# ------------------------------------------------------------------ contas
@router.get("/accounts")
def list_accounts(
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    return [a.model_dump(mode="json") for a in memory.finance.list_accounts(user_id)]


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
def create_account(
    body: FinanceAccountCreate,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    acc = memory.finance.create_account(
        FinanceAccount(user_id=user_id, **body.model_dump())
    )
    return acc.model_dump(mode="json")


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    account_id: str,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    if not memory.finance.delete_account(user_id, account_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conta não encontrada")


# ------------------------------------------------------------- lançamentos
@router.get("/transactions")
def list_transactions(
    account_id: str | None = None,
    limit: int = 50,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    items = memory.finance.list_transactions(user_id, account_id=account_id, limit=limit)
    return [t.model_dump(mode="json") for t in items]


@router.post("/transactions", status_code=status.HTTP_201_CREATED)
def create_transaction(
    body: FinanceTransactionCreate,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    data = body.model_dump()
    occurred = data.pop("occurred_at") or utcnow()
    tx = FinanceTransaction(user_id=user_id, occurred_at=occurred, **data)
    try:
        memory.finance.add_transaction(tx)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return tx.model_dump(mode="json")


# ------------------------------------------------------------------- metas
@router.get("/goals")
def list_goals(
    status_filter: str | None = None,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    goals = memory.finance.list_goals(user_id, status=status_filter)
    return [{**g.model_dump(mode="json"), "progress": g.progress} for g in goals]


@router.post("/goals", status_code=status.HTTP_201_CREATED)
def create_goal(
    body: FinanceGoalCreate,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    goal = memory.finance.create_goal(FinanceGoal(user_id=user_id, **body.model_dump()))
    return {**goal.model_dump(mode="json"), "progress": goal.progress}


class GoalProgressBody(BaseModel):
    current_amount: float = Field(ge=0)


@router.patch("/goals/{goal_id}")
def update_goal(
    goal_id: str,
    body: GoalProgressBody,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    goal = memory.finance.update_goal_progress(user_id, goal_id, body.current_amount)
    if not goal:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "meta não encontrada")
    return {**goal.model_dump(mode="json"), "progress": goal.progress}


@router.delete("/goals/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_goal(
    goal_id: str,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    if not memory.finance.delete_goal(user_id, goal_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "meta não encontrada")
