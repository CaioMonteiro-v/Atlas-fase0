"""API — Ayra do dia seguinte."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import get_llm, get_memory
from app.ayra.morning import MorningBriefingService
from app.core.security import current_user_id
from app.llm.base import LLM
from app.memory.service import MemoryService

router = APIRouter(prefix="/ayra", tags=["ayra-dia"])


def _svc(memory: MemoryService, llm: LLM) -> MorningBriefingService:
    return MorningBriefingService(memory.db, memory, llm)


@router.get("/dia-seguinte")
async def get_dia_seguinte(
    force: bool = False,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
    llm: LLM = Depends(get_llm),
):
    """Devolve (e cria se preciso) o empurrão de hoje — uma ação só."""
    brief = await _svc(memory, llm).build(user_id, force=force)
    return brief.model_dump(mode="json")


@router.post("/dia-seguinte/start")
async def start_dia_seguinte(
    force: bool = False,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
    llm: LLM = Depends(get_llm),
):
    """Abre sessão com a Ayra já no foco do dia."""
    svc = _svc(memory, llm)
    brief = await svc.build(user_id, force=force)
    return svc.start_payload(brief)


class BriefStatusBody(BaseModel):
    status: str = Field(pattern="^(pending|done|skipped)$")


@router.post("/dia-seguinte/status")
async def set_status(
    body: BriefStatusBody,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
    llm: LLM = Depends(get_llm),
):
    try:
        brief = _svc(memory, llm).mark(user_id, body.status)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if not brief:
        # gera e marca
        brief = await _svc(memory, llm).build(user_id)
        brief = _svc(memory, llm).mark(user_id, body.status)
    if not brief:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "briefing não encontrado")
    return brief.model_dump(mode="json")
