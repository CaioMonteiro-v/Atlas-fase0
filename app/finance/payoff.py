"""
Plano anti-dívida: avalanche / bola de neve + cortes de orçamento.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from app.domain.models import DebtPayoffPlan, FinanceDebt, PayoffStrategy
from app.llm.base import LLM
from app.memory.service import MemoryService

log = logging.getLogger("atlas.finance.payoff")

POLISH_SYSTEM = """Você é a Ayra, consultora financeira dura e clara.

Dado o plano de quitação já calculado, escreva:
- summary: 2-3 frases com o diagnóstico e a ordem de ataque
- chat_opener: mensagem para abrir a conversa (o que cortar + qual dívida atacar primeiro)

Português do Brasil. Sem emojis. Sem enrolação. Números concretos.
"""


class DebtPlanPolish(BaseModel):
    summary: str = ""
    chat_opener: str = ""


def _estimate_months(debts: list[FinanceDebt], extra: float, strategy: PayoffStrategy) -> int | None:
    """Simulação simples mês a mês (juros + pagamento)."""
    if not debts:
        return 0
    bals = {d.id: d.balance for d in debts}
    rates = {d.id: d.interest_rate_month / 100.0 for d in debts}
    mins = {d.id: max(d.installment, 0.0) for d in debts}

    def order_ids() -> list[str]:
        active = [d for d in debts if bals[d.id] > 0.01]
        if strategy == "bola_de_neve":
            active.sort(key=lambda d: (bals[d.id], -d.interest_rate_month))
        else:
            active.sort(key=lambda d: (-d.interest_rate_month, bals[d.id]))
        return [d.id for d in active]

    months = 0
    for _ in range(600):  # teto 50 anos
        ids = order_ids()
        if not ids:
            return months
        months += 1
        # juros
        for i in ids:
            bals[i] *= 1 + rates[i]
        # mínimas
        pool = extra
        for i in ids:
            pay = min(mins[i], bals[i])
            bals[i] -= pay
        # extra na primeira da fila
        target = ids[0]
        if pool > 0 and bals[target] > 0:
            pay = min(pool, bals[target])
            bals[target] -= pay
    return None


async def build_payoff_plan(
    memory: MemoryService,
    llm: LLM,
    user_id: str,
    *,
    strategy: PayoffStrategy = "avalanche",
    extra_payment: float = 0.0,
    income_hint: float | None = None,
) -> DebtPayoffPlan:
    debts = memory.finance.list_debts(user_id, status="ativa")
    health = memory.finance.health(user_id)
    budget = memory.finance.budget_status(user_id)
    report = memory.finance.monthly_report(user_id)

    if strategy == "bola_de_neve":
        ordered = sorted(debts, key=lambda d: (d.balance, -d.interest_rate_month))
    else:
        ordered = sorted(debts, key=lambda d: (-d.interest_rate_month, d.balance))

    total = sum(d.balance for d in debts)
    mins = sum(d.installment for d in debts)

    # sobra natural do mês (se houver) vira extra sugerido
    natural = max(0.0, health.poupanca_mes)
    if income_hint is not None and income_hint > 0:
        natural = max(natural, income_hint - health.despesa_mes - mins)
    firepower = mins + max(extra_payment, 0.0)
    months = _estimate_months(debts, max(extra_payment, 0.0), strategy) if debts else 0

    order = []
    for i, d in enumerate(ordered):
        order.append({
            "rank": i + 1,
            "id": d.id,
            "name": d.name,
            "kind": d.kind,
            "balance": d.balance,
            "interest_rate_month": d.interest_rate_month,
            "installment": d.installment,
            "role": "ataque" if i == 0 else "mínima",
        })

    cuts: list[dict[str, Any]] = []
    for row in budget.get("caps", []):
        if row.get("estourada"):
            over = round(float(row["gasto"]) - float(row["limite"]), 2)
            cuts.append({
                "categoria": row["categoria"],
                "cortar": over,
                "motivo": "estourou o teto do orçamento",
            })
    # top categorias sem teto
    if len(cuts) < 3:
        for cat in report.get("por_categoria", [])[:5]:
            if any(c["categoria"] == cat["categoria"] for c in cuts):
                continue
            if cat["total"] < 50:
                continue
            cuts.append({
                "categoria": cat["categoria"],
                "cortar": round(cat["total"] * 0.15, 2),
                "motivo": "alto volume — corte 15% este mês",
            })
            if len(cuts) >= 3:
                break

    suggested_extra = round(max(extra_payment, min(natural, 500.0) if natural else extra_payment), 2)
    first = order[0]["name"] if order else "nenhuma dívida"

    base_summary = (
        f"Você tem R$ {total:,.2f} em dívidas ativas e R$ {mins:,.2f}/mês em parcelas. "
        f"Estratégia {strategy}: ataque primeiro «{first}». "
        f"Com R$ {suggested_extra:,.2f} extra/mês, "
        + (f"estimativa ~{months} meses." if months is not None else "o prazo ainda é longo — aumente o extra.")
    ).replace(",", "X").replace(".", ",").replace("X", ".")

    plan = DebtPayoffPlan(
        strategy=strategy,
        extra_payment=suggested_extra,
        total_debt=round(total, 2),
        min_payments=round(mins, 2),
        monthly_firepower=round(firepower + max(0, suggested_extra - extra_payment), 2),
        months_estimate=months,
        order=order,
        cuts=cuts[:5],
        summary=base_summary,
        chat_opener=(
            f"Quero sair das dívidas. Total R$ {total:.2f}, parcelas R$ {mins:.2f}/mês. "
            f"Estratégia {strategy}, extra R$ {suggested_extra:.2f}. "
            f"Primeira alvo: {first}. "
            f"Cortes sugeridos: "
            + (", ".join(f"{c['categoria']} (−R$ {c['cortar']:.2f})" for c in cuts[:3]) or "definir orçamento")
            + ". Monte o plano de guerra do mês e me cobre."
        ),
    )

    try:
        polish = await llm.extract(
            POLISH_SYSTEM,
            (
                f"Resumo base: {plan.summary}\n"
                f"Ordem: {order}\nCortes: {cuts[:5]}\n"
                f"Receita mês: {health.receita_mes} Despesa: {health.despesa_mes}\n"
            ),
            DebtPlanPolish,
        )
        if polish.summary:
            plan.summary = polish.summary
        if polish.chat_opener:
            plan.chat_opener = polish.chat_opener
    except Exception as exc:
        log.warning("polish do plano de dívidas falhou: %s", exc)

    return plan
