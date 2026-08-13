"""API do Domínio Financeiro (Cap. 82–92)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import get_llm, get_memory, get_planner
from app.ayra.orchestrator import JourneyPlanner
from app.core.security import current_user_id
from app.domain.models import (
    DebtPayoffRequest,
    FinanceAccount,
    FinanceAccountCreate,
    FinanceBudgetCap,
    FinanceBudgetCapCreate,
    FinanceDebt,
    FinanceDebtCreate,
    FinanceDebtUpdate,
    FinanceGoal,
    FinanceGoalCreate,
    FinanceTransaction,
    FinanceTransactionCreate,
    Journey,
    PersonalMemory,
    Privacy,
    StartFinanceWithAyra,
    new_id,
    utcnow,
)
from app.finance.payoff import build_payoff_plan
from app.llm.base import LLM
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


@router.get("/report")
def monthly_report(
    month: str | None = None,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    """Fluxo do mês por categoria (receitas, despesas, poupança)."""
    return memory.finance.monthly_report(user_id, month=month)


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
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return tx.model_dump(mode="json")


@router.delete("/transactions/{tx_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction(
    tx_id: str,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    if not memory.finance.delete_transaction(user_id, tx_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lançamento não encontrado")


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


# ------------------------------------------------------------------- dívidas
@router.get("/debts")
def list_debts(
    status_filter: str | None = "ativa",
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    return [d.model_dump(mode="json") for d in memory.finance.list_debts(user_id, status=status_filter)]


@router.post("/debts", status_code=status.HTTP_201_CREATED)
def create_debt(
    body: FinanceDebtCreate,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    debt = memory.finance.create_debt(FinanceDebt(user_id=user_id, **body.model_dump()))
    return debt.model_dump(mode="json")


@router.patch("/debts/{debt_id}")
def patch_debt(
    debt_id: str,
    body: FinanceDebtUpdate,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    updated = memory.finance.update_debt(user_id, debt_id, body.model_dump(exclude_none=True))
    if not updated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dívida não encontrada")
    return updated.model_dump(mode="json")


@router.delete("/debts/{debt_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_debt(
    debt_id: str,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    if not memory.finance.delete_debt(user_id, debt_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dívida não encontrada")


# ----------------------------------------------------------------- orçamento
@router.get("/budget")
def get_budget(
    month: str | None = None,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    return memory.finance.budget_status(user_id, month=month)


@router.put("/budget")
def put_budget_cap(
    body: FinanceBudgetCapCreate,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    month = body.month or memory.finance.current_month()
    cap = memory.finance.upsert_budget_cap(
        FinanceBudgetCap(
            user_id=user_id,
            month=month,
            category=body.category.strip().lower(),
            limit_amount=body.limit_amount,
        )
    )
    return {
        "cap": cap.model_dump(mode="json"),
        "status": memory.finance.budget_status(user_id, month=month),
    }


@router.delete("/budget/{cap_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_budget_cap(
    cap_id: str,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    if not memory.finance.delete_budget_cap(user_id, cap_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "teto não encontrado")


# ----------------------------------------------------------- plano anti-dívida
@router.post("/payoff-plan")
async def payoff_plan(
    body: DebtPayoffRequest,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
    llm: LLM = Depends(get_llm),
):
    """Calcula ordem de quitação + cortes e devolve opener para a Ayra."""
    plan = await build_payoff_plan(
        memory, llm, user_id,
        strategy=body.strategy,
        extra_payment=body.extra_payment,
        income_hint=body.income_hint,
    )
    return plan.model_dump(mode="json")


@router.post("/payoff-plan/start")
async def payoff_plan_start(
    body: DebtPayoffRequest,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
    llm: LLM = Depends(get_llm),
    planner: JourneyPlanner = Depends(get_planner),
):
    """Gera plano + abre jornada/sessão com a Ayra no modo guerra às dívidas."""
    plan = await build_payoff_plan(
        memory, llm, user_id,
        strategy=body.strategy,
        extra_payment=body.extra_payment,
        income_hint=body.income_hint,
    )
    memory.personal.create(
        PersonalMemory(
            user_id=user_id,
            category="objetivos",
            content={"texto": f"Quitar dívidas ({body.strategy}) — total R$ {plan.total_debt:.2f}"},
            privacy=Privacy.PRIVATE,
            confidence=1.0,
            tags=["financas", "dividas"],
        )
    )
    journey = memory.journeys.create(
        Journey(
            user_id=user_id,
            domain="financas",
            title="Sair das dívidas",
            stated_goal="Reduzir gastos e quitar empréstimos",
            status="descobrindo",
        )
    )
    planned = await planner.plan(user_id, journey.id)
    journey = planned or memory.journeys.get(user_id, journey.id)
    session_id = new_id()
    memory.conversation.ensure_session(user_id, session_id, journey_id=journey.id)
    return {
        "plan": plan.model_dump(mode="json"),
        "journey": {**journey.model_dump(mode="json"), "progress": journey.progress},
        "session_id": session_id,
        "mensagem_sugerida": plan.chat_opener,
    }


# ---------------------------------------------------------- coach com Ayra
@router.post("/start-with-ayra")
async def start_with_ayra(
    body: StartFinanceWithAyra,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
    planner: JourneyPlanner = Depends(get_planner),
):
    """Organizar finanças com a Ayra: jornada + sessão + mensagem inicial."""
    focus_label = {
        "organizar": "organizar o mês",
        "reserva": "montar reserva de emergência",
        "dividas": "sair das dívidas",
        "investir": "começar a investir com segurança",
        "orcamento": "fechar um orçamento realista",
    }.get(body.focus, body.goal)

    memory.personal.create(
        PersonalMemory(
            user_id=user_id,
            category="objetivos",
            content={"texto": f"Finanças: {body.goal}"},
            privacy=Privacy.PRIVATE,
            confidence=1.0,
            tags=["financas", "mentoria"],
        )
    )

    journey = memory.journeys.create(
        Journey(
            user_id=user_id,
            domain="financas",
            title=f"Finanças — {focus_label}",
            stated_goal=body.goal,
            status="descobrindo",
        )
    )
    planned = await planner.plan(user_id, journey.id)
    journey = planned or memory.journeys.get(user_id, journey.id)

    session_id = new_id()
    memory.conversation.ensure_session(user_id, session_id, journey_id=journey.id)
    health = memory.finance.health(user_id)
    debts = memory.finance.list_debts(user_id, status="ativa")
    debt_line = (
        f" Dívidas: R$ {health.dividas_total:.2f} em {len(debts)} contrato(s), "
        f"parcelas R$ {health.parcelas_mes:.2f}/mês."
        if debts else " Sem dívidas cadastradas ainda."
    )

    return {
        "journey": {**journey.model_dump(mode="json"), "progress": journey.progress},
        "session_id": session_id,
        "health": health.model_dump(mode="json"),
        "mensagem_sugerida": (
            f"Quero que você seja minha consultora financeira. Objetivo: {body.goal}. "
            f"Foco: {focus_label}. "
            f"Situação agora: patrimônio R$ {health.patrimonio:.2f}, "
            f"receita do mês R$ {health.receita_mes:.2f}, despesa R$ {health.despesa_mes:.2f}, "
            f"taxa de poupança {health.taxa_poupanca:.0%}, "
            f"reserva {health.reserva_meses if health.reserva_meses is not None else 'n/d'} meses."
            f"{debt_line} "
            "Diagnostique com clareza e proponha o próximo passo concreto."
        ),
    }
