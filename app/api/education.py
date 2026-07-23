"""API do Domínio Educação — estudo geral com Ayra mentora (Cap. 69–81)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import get_memory, get_planner
from app.ayra.orchestrator import JourneyPlanner
from app.core.security import current_user_id
from app.domain.models import (
    SUBJECT_SUGGESTIONS,
    Competency,
    CompetencyCreate,
    Journey,
    PersonalMemory,
    Privacy,
    StartStudyWithAyra,
    StudyNote,
    StudyNoteCreate,
    StudySession,
    StudySessionCreate,
    StudyTrack,
    StudyTrackCreate,
    new_id,
    utcnow,
)
from app.memory.service import MemoryService

router = APIRouter(prefix="/education", tags=["educacao"])


@router.get("/areas")
def list_areas():
    """Sugestões — a área é texto livre. Qualquer assunto do conhecimento humano."""
    return {
        "livre": True,
        "sugestoes": list(SUBJECT_SUGGESTIONS),
        "dica": "Digite qualquer área: Direito Constitucional, Cálculo 1, Fisioterapia, Psicologia…",
    }


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
    area = (body.subject_area or "geral").strip()[:120] or "geral"
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


# -------------------- começar com a Ayra mentora (o fluxo principal)
@router.post("/start-with-ayra")
async def start_with_ayra(
    body: StartStudyWithAyra,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
    planner: JourneyPlanner = Depends(get_planner),
):
    """Quero aprender X → trilha + jornada + sessão com a Ayra ensinando.

    Ex.: topic='Direito Constitucional' | 'Cálculo 1' | 'Fisioterapia respiratória'
    """
    topic = body.topic.strip()
    area = (body.subject_area or topic).strip()[:120]
    goal = (body.goal or f"Aprender {topic} com compreensão real").strip()

    memory.personal.create(
        PersonalMemory(
            user_id=user_id,
            category="objetivos",
            content={"texto": f"Estudar: {topic}" + (f" — {goal}" if body.goal else "")},
            privacy=Privacy.PRIVATE,
            confidence=1.0,
            tags=["estudo", "mentoria"],
        )
    )

    track = memory.education.create_track(
        StudyTrack(
            user_id=user_id,
            title=topic,
            subject_area=area,
            level=body.level,
            goal=goal,
            status="ativa",
        )
    )

    journey = memory.journeys.create(
        Journey(
            user_id=user_id,
            domain="educacao",
            title=f"Aprender {topic}",
            stated_goal=goal,
            status="descobrindo",
        )
    )
    planned = await planner.plan(user_id, journey.id)
    journey = planned or memory.journeys.get(user_id, journey.id)
    memory.education.link_journey(user_id, track.id, journey.id)

    session_id = new_id()
    memory.conversation.ensure_session(user_id, session_id, journey_id=journey.id)

    return {
        "track": track.model_dump(mode="json"),
        "journey": {**journey.model_dump(mode="json"), "progress": journey.progress},
        "session_id": session_id,
        "mensagem_sugerida": (
            f"Quero que você seja minha mentora em «{topic}». "
            f"Meu nível é {body.level}. Objetivo: {goal}. "
            "Comece pelo fundamento mais importante, explique com clareza, "
            "dê um exemplo e no fim me faça uma pergunta para checar se eu entendi. "
            "Depois eu anoto o que aprendi no caderno."
        ),
    }


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


# ------------------------------------------------------ caderno de anotações
@router.get("/notes")
def list_notes(
    track_id: str | None = None,
    limit: int = 50,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    return [n.model_dump(mode="json") for n in memory.education.list_notes(user_id, track_id=track_id, limit=limit)]


@router.post("/notes", status_code=status.HTTP_201_CREATED)
def create_note(
    body: StudyNoteCreate,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    note = StudyNote(user_id=user_id, **body.model_dump())
    try:
        memory.education.create_note(note)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return note.model_dump(mode="json")


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    note_id: str,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    if not memory.education.delete_note(user_id, note_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "anotação não encontrada")


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
    area = (body.subject_area or "geral").strip()[:120] or "geral"
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
