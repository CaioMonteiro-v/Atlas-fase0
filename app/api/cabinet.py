"""API do Domínio Gabinete Inteligente (Cap. 97–105)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_memory, get_planner
from app.ayra.orchestrator import JourneyPlanner
from app.core.security import current_user_id
from app.domain.models import (
    CabinetAgendaCreate,
    CabinetAgendaItem,
    CabinetAgendaUpdate,
    CabinetCitizen,
    CabinetCitizenCreate,
    CabinetDemand,
    CabinetDemandCreate,
    CabinetDemandUpdate,
    CabinetTimelineCreate,
    CabinetTimelineEvent,
    Journey,
    PersonalMemory,
    Privacy,
    StartCabinetWithAyra,
    new_id,
    utcnow,
)
from app.memory.service import MemoryService

router = APIRouter(prefix="/cabinet", tags=["gabinete"])


@router.get("/snapshot")
def snapshot(
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    return memory.cabinet.snapshot(user_id).model_dump(mode="json")


# ---------------------------------------------------------------- cidadãos
@router.get("/citizens")
def list_citizens(
    municipality: str | None = None,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    return [c.model_dump(mode="json") for c in memory.cabinet.list_citizens(user_id, municipality)]


@router.get("/citizens/{citizen_id}")
def get_citizen(
    citizen_id: str,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    c = memory.cabinet.get_citizen(user_id, citizen_id)
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cidadão não encontrado")
    timeline = memory.cabinet.list_timeline(user_id, citizen_id=citizen_id, limit=40)
    demands = [
        d for d in memory.cabinet.list_demands(user_id, limit=100)
        if d.citizen_id == citizen_id
    ]
    return {
        "citizen": c.model_dump(mode="json"),
        "timeline": [e.model_dump(mode="json") for e in timeline],
        "demands": [d.model_dump(mode="json") for d in demands],
    }


@router.post("/citizens", status_code=status.HTTP_201_CREATED)
def create_citizen(
    body: CabinetCitizenCreate,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    c = memory.cabinet.create_citizen(CabinetCitizen(user_id=user_id, **body.model_dump()))
    return c.model_dump(mode="json")


@router.delete("/citizens/{citizen_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_citizen(
    citizen_id: str,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    if not memory.cabinet.delete_citizen(user_id, citizen_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cidadão não encontrado")


# ---------------------------------------------------------------- demandas
@router.get("/demands")
def list_demands(
    status_filter: str | None = None,
    municipality: str | None = None,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    items = memory.cabinet.list_demands(user_id, status=status_filter, municipality=municipality)
    return [d.model_dump(mode="json") for d in items]


@router.post("/demands", status_code=status.HTTP_201_CREATED)
def create_demand(
    body: CabinetDemandCreate,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    try:
        d = memory.cabinet.create_demand(CabinetDemand(user_id=user_id, **body.model_dump()))
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    memory.cabinet.add_timeline(
        CabinetTimelineEvent(
            user_id=user_id,
            citizen_id=d.citizen_id,
            demand_id=d.id,
            municipality=d.municipality,
            event_type="demanda",
            title=f"Demanda aberta: {d.title}",
            description=d.subject,
        )
    )
    return d.model_dump(mode="json")


@router.patch("/demands/{demand_id}")
def update_demand(
    demand_id: str,
    body: CabinetDemandUpdate,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    before = memory.cabinet.get_demand(user_id, demand_id)
    updated = memory.cabinet.update_demand(user_id, demand_id, body.model_dump(exclude_none=True))
    if not updated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "demanda não encontrada")
    if before and body.status and body.status != before.status:
        memory.cabinet.add_timeline(
            CabinetTimelineEvent(
                user_id=user_id,
                citizen_id=updated.citizen_id,
                demand_id=updated.id,
                municipality=updated.municipality,
                event_type="retorno",
                title=f"Status: {before.status} → {updated.status}",
                description=updated.result or updated.subject,
            )
        )
    return updated.model_dump(mode="json")


@router.delete("/demands/{demand_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_demand(
    demand_id: str,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    if not memory.cabinet.delete_demand(user_id, demand_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "demanda não encontrada")


# ------------------------------------------------------------ linha do tempo
@router.get("/timeline")
def list_timeline(
    citizen_id: str | None = None,
    limit: int = 40,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    items = memory.cabinet.list_timeline(user_id, citizen_id=citizen_id, limit=limit)
    return [e.model_dump(mode="json") for e in items]


@router.post("/timeline", status_code=status.HTTP_201_CREATED)
def create_timeline(
    body: CabinetTimelineCreate,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    data = body.model_dump()
    occurred = data.pop("occurred_at") or utcnow()
    event = CabinetTimelineEvent(user_id=user_id, occurred_at=occurred, **data)
    memory.cabinet.add_timeline(event)
    return event.model_dump(mode="json")


# ------------------------------------------------------------------ agenda
@router.get("/agenda")
def list_agenda(
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    return [a.model_dump(mode="json") for a in memory.cabinet.list_agenda(user_id)]


@router.post("/agenda", status_code=status.HTTP_201_CREATED)
def create_agenda(
    body: CabinetAgendaCreate,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    item = memory.cabinet.create_agenda(CabinetAgendaItem(user_id=user_id, **body.model_dump()))
    return item.model_dump(mode="json")


@router.patch("/agenda/{item_id}")
def update_agenda(
    item_id: str,
    body: CabinetAgendaUpdate,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    updated = memory.cabinet.update_agenda(
        user_id, item_id, status=body.status, notes=body.notes
    )
    if not updated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "compromisso não encontrado")
    return updated.model_dump(mode="json")


# ---------------------------------------------------------- assessoria Ayra
@router.post("/start-with-ayra")
async def start_with_ayra(
    body: StartCabinetWithAyra,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
    planner: JourneyPlanner = Depends(get_planner),
):
    """Assessoria parlamentar / gabinete com a Ayra."""
    demand = None
    if body.demand_id:
        demand = memory.cabinet.get_demand(user_id, body.demand_id)

    memory.personal.create(
        PersonalMemory(
            user_id=user_id,
            category="objetivos",
            content={"texto": f"Gabinete: {body.topic}"},
            privacy=Privacy.PRIVATE,
            confidence=1.0,
            tags=["gabinete", "mentoria"],
        )
    )

    title = f"Gabinete — {body.municipality}" if body.municipality else "Gabinete Inteligente"
    journey = memory.journeys.create(
        Journey(
            user_id=user_id,
            domain="gabinete",
            title=title[:200],
            stated_goal=body.topic,
            status="descobrindo",
        )
    )
    planned = await planner.plan(user_id, journey.id)
    journey = planned or memory.journeys.get(user_id, journey.id)

    session_id = new_id()
    memory.conversation.ensure_session(user_id, session_id, journey_id=journey.id)
    snap = memory.cabinet.snapshot(user_id)

    demanda_ctx = ""
    if demand:
        demanda_ctx = (
            f" Demanda em foco: «{demand.title}» ({demand.status}, prioridade {demand.priority})"
            f" em {demand.municipality or 's/ município'}."
        )

    return {
        "journey": {**journey.model_dump(mode="json"), "progress": journey.progress},
        "session_id": session_id,
        "snapshot": snap.model_dump(mode="json"),
        "mensagem_sugerida": (
            f"Quero que você seja minha assessora de gabinete. Assunto: {body.topic}."
            f"{demanda_ctx} "
            f"Painel: {snap.demandas_abertas} abertas, {snap.demandas_urgentes} urgentes, "
            f"{snap.demandas_atrasadas} atrasadas. "
            "Me diga o próximo movimento político/administrativo concreto."
        ),
    }
