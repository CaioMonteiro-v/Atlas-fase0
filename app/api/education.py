"""API do Domínio Educação — estudo geral (Cap. 69–81)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import get_memory
from app.core.security import current_user_id
from app.domain.models import (
    SUBJECT_AREAS,
    Competency,
    CompetencyCreate,
    StudySession,
    StudySessionCreate,
    StudyTrack,
    StudyTrackCreate,
    utcnow,
)
from app.memory.service import MemoryService

router = APIRouter(prefix="/education", tags=["educacao"])


@router.get("/areas")
def list_areas():
    """Áreas canônicas — estudo é geral; idiomas é só uma delas."""
    return {"areas": list(SUBJECT_AREAS)}


@router.get("/snapshot")
def snapshot(
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    return memory.education.snapshot(user_id).model_dump(mode="json")


# ------------------------------------------------------------------- trilhas
@router.get("/tracks")
def list_tracks(
    status_filter: str | None = None,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    return [t.model_dump(mode="json") for t in memory.education.list_tracks(user_id, status=status_filter)]


@router.post("/tracks", status_code=status.HTTP_201_CREATED)
def create_track(
    body: StudyTrackCreate,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    area = body.subject_area if body.subject_area in SUBJECT_AREAS else "outro"
    track = memory.education.create_track(
        StudyTrack(user_id=user_id, **{**body.model_dump(), "subject_area": area})
    )
    return track.model_dump(mode="json")


@router.delete("/tracks/{track_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_track(
    track_id: str,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    if not memory.education.delete_track(user_id, track_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "trilha não encontrada")


# ------------------------------------------------------------------ sessões
@router.get("/sessions")
def list_sessions(
    track_id: str | None = None,
    limit: int = 30,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    items = memory.education.list_sessions(user_id, track_id=track_id, limit=limit)
    return [s.model_dump(mode="json") for s in items]


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_session(
    body: StudySessionCreate,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    data = body.model_dump()
    occurred = data.pop("occurred_at") or utcnow()
    session = StudySession(user_id=user_id, occurred_at=occurred, **data)
    try:
        memory.education.add_session(session)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return session.model_dump(mode="json")


# ------------------------------------------------------------- competências
@router.get("/competencies")
def list_competencies(
    status_filter: str | None = None,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    items = memory.education.list_competencies(user_id, status=status_filter)
    return [c.model_dump(mode="json") for c in items]


@router.post("/competencies", status_code=status.HTTP_201_CREATED)
def create_competency(
    body: CompetencyCreate,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    area = body.subject_area if body.subject_area in SUBJECT_AREAS else "outro"
    comp = memory.education.create_competency(
        Competency(user_id=user_id, **{**body.model_dump(), "subject_area": area})
    )
    return comp.model_dump(mode="json")


class CompetencyPatch(BaseModel):
    level: str | None = None
    status: str | None = None
    evidence: str | None = None


@router.patch("/competencies/{comp_id}")
def patch_competency(
    comp_id: str,
    body: CompetencyPatch,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    updated = memory.education.update_competency(
        user_id, comp_id, level=body.level, status=body.status, evidence=body.evidence
    )
    if not updated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "competência não encontrada")
    return updated.model_dump(mode="json")
