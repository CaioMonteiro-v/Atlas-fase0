"""Jornadas (Cap. 19/20) e ingestão de conhecimento (Cap. 32)."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.api.deps import get_ingestor, get_llm, get_memory, get_planner
from app.ayra.orchestrator import JourneyPlanner
from app.core.security import current_user_id
from app.domain.models import Journey, JourneyCreate, JourneyStatusUpdate, JourneyStep
from app.knowledge.ingest import DocumentIngestor
from app.llm.base import LLM
from app.memory.service import MemoryService

router = APIRouter(tags=["jornadas"])


# --------------------------------------------------------------------------
# Jornadas
# --------------------------------------------------------------------------
@router.post("/journeys", status_code=status.HTTP_201_CREATED)
def create_journey(
    body: JourneyCreate,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    j = memory.journeys.create(Journey(user_id=user_id, **body.model_dump(), status="descobrindo"))
    return j.model_dump(mode="json")


@router.get("/journeys")
def list_journeys(
    status_filter: str | None = None,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    items = memory.journeys.list(user_id, status=status_filter)
    return [{**j.model_dump(mode="json"), "progress": j.progress} for j in items]


@router.get("/journeys/{journey_id}")
def get_journey(
    journey_id: str,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    j = memory.journeys.get(user_id, journey_id)
    if not j:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "jornada não encontrada")
    return {**j.model_dump(mode="json"), "progress": j.progress}


@router.patch("/journeys/{journey_id}")
def patch_journey(
    journey_id: str,
    body: JourneyStatusUpdate,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    if not memory.journeys.set_status(user_id, journey_id, body.status):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "jornada não encontrada")
    j = memory.journeys.get(user_id, journey_id)
    return {**j.model_dump(mode="json"), "progress": j.progress}


class DiagnosisBody(BaseModel):
    real_goal: str = Field(min_length=1)
    diagnosis: dict = Field(default_factory=dict)


@router.post("/journeys/{journey_id}/diagnose")
def diagnose(
    journey_id: str,
    body: DiagnosisBody,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    """Etapa 1+2 do Cap. 20. É aqui que 'quero estudar Excel' vira
    'quero conseguir um emprego' e a jornada passa a ser construída sobre o
    objetivo verdadeiro."""
    if not memory.journeys.set_goals(user_id, journey_id, body.real_goal, body.diagnosis):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "jornada não encontrada")
    return memory.journeys.get(user_id, journey_id).model_dump(mode="json")


class StepBody(BaseModel):
    title: str
    description: str = ""


@router.post("/journeys/{journey_id}/steps", status_code=status.HTTP_201_CREATED)
def add_steps(
    journey_id: str,
    body: list[StepBody],
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    if not memory.journeys.get(user_id, journey_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "jornada não encontrada")
    steps = [
        JourneyStep(journey_id=journey_id, user_id=user_id, order_index=0,
                    title=s.title, description=s.description)
        for s in body
    ]
    memory.journeys.add_steps(user_id, journey_id, steps)
    return [s.model_dump(mode="json") for s in steps]


@router.patch("/journeys/{journey_id}/steps/{step_id}")
def update_step(
    journey_id: str,
    step_id: str,
    new_status: str = "concluida",
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    if new_status not in {"pendente", "em_progresso", "concluida", "pulada"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "status inválido")
    if not memory.journeys.set_step_status(user_id, step_id, new_status):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "passo não encontrado")
    j = memory.journeys.get(user_id, journey_id)
    return {"progress": j.progress if j else 0.0}


@router.post("/journeys/{journey_id}/plan")
async def plan_journey(
    journey_id: str,
    user_id: str = Depends(current_user_id),
    planner: JourneyPlanner = Depends(get_planner),
):
    """Cap. 20 Etapa 3 — a Ayra descobre o objetivo real, diagnostica e monta os passos."""
    journey = await planner.plan(user_id, journey_id)
    if not journey:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "jornada não encontrada")
    return {**journey.model_dump(mode="json"), "progress": journey.progress}


@router.delete("/journeys/{journey_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_journey(
    journey_id: str,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    if not memory.journeys.delete(user_id, journey_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "jornada não encontrada")


# --------------------------------------------------------------------------
# Conhecimento
# --------------------------------------------------------------------------
@router.post("/knowledge/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest(
    background: BackgroundTasks,
    file: UploadFile,
    user_id: str = Depends(current_user_id),
    ingestor: DocumentIngestor = Depends(get_ingestor),
    llm: LLM = Depends(get_llm),
):
    """202 Accepted: a ingestão roda em background. PDF escaneado usa OCR."""
    from app.knowledge.extract import extract_document

    raw = await file.read()
    if len(raw) > 12_000_000:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "arquivo acima de 12 MB")

    title = file.filename or "documento"
    ocr_fn = llm.ocr if hasattr(llm, "ocr") else None
    try:
        text, fmt = await extract_document(title, raw, ocr=ocr_fn)
    except ValueError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc

    if len(text.strip()) < 40:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "documento sem conteúdo suficiente")

    background.add_task(
        ingestor.ingest, user_id, title, text, {"origem": "upload", "formato": fmt}
    )
    return {"status": "processando", "titulo": title, "formato": fmt, "caracteres": len(text)}


@router.get("/knowledge/nodes")
def list_nodes(
    limit: int = 40,
    node_type: str | None = None,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    nodes = memory.knowledge.list_nodes(user_id, limit=limit, node_type=node_type)
    return [n.model_dump(mode="json") for n in nodes]


@router.delete("/knowledge/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_node(
    node_id: str,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    if not memory.knowledge.delete_node(user_id, node_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "nó não encontrado")


@router.get("/knowledge/search")
async def search_knowledge(
    q: str,
    top_k: int = 6,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    vec, model = None, None
    try:
        vecs = await memory.llm.embed([q])
        vec, model = vecs[0], memory.llm.embedding_model
    except Exception:
        pass
    hits = memory.knowledge.search(user_id, q, query_embedding=vec, model=model, top_k=top_k)
    return [
        {"id": h.node.id, "titulo": h.node.title, "tipo": h.node.node_type,
         "descricao": h.node.description, "score": h.score, "match": h.matched_by}
        for h in hits
    ]


@router.get("/knowledge/{node_id}/neighbors")
def neighbors(
    node_id: str,
    rel_type: str | None = None,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    node = memory.knowledge.get_node(user_id, node_id)
    if not node:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "nó não encontrado")
    viz = memory.knowledge.neighbors(user_id, node_id, rel_type)
    return {
        "node": node.model_dump(mode="json"),
        "neighbors": [n.model_dump(mode="json") for n in viz],
    }
