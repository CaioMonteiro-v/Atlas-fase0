"""API do Domínio Educação — estudo geral com Ayra mentora (Cap. 69–81)."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.api.deps import get_ingestor, get_llm, get_memory, get_planner, get_study_mentor
from app.ayra.orchestrator import JourneyPlanner
from app.core.security import current_user_id
from app.domain.models import (
    SUBJECT_SUGGESTIONS,
    Competency,
    CompetencyCreate,
    Journey,
    PersonalMemory,
    Privacy,
    QuizSubmit,
    ReviewGrade,
    StartStudyWithAyra,
    StudyMaterial,
    StudyNote,
    StudyNoteCreate,
    StudySession,
    StudySessionCreate,
    StudyTrack,
    StudyTrackCreate,
    StudyWeeklyPlan,
    StudyWeeklyPlanCreate,
    new_id,
    utcnow,
)
from app.education.mentor import StudyMentor
from app.knowledge.ingest import DocumentIngestor
from app.llm.base import LLM
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
    mentor: StudyMentor = Depends(get_study_mentor),
):
    """Quero aprender X → trilha + capítulos + jornada + sessão com a Ayra ensinando.

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

    chapters = await mentor.generate_chapters(user_id, track.id)
    first = chapters[0] if chapters else None
    if first:
        memory.education.set_chapter_status(user_id, first.id, "em_progresso")

    session_id = new_id()
    memory.conversation.ensure_session(user_id, session_id, journey_id=journey.id)

    first_bit = (
        f"Comece pelo capítulo 1: «{first.title}». {first.summary} "
        if first else "Comece pelo fundamento mais importante. "
    )

    return {
        "track": track.model_dump(mode="json"),
        "journey": {**journey.model_dump(mode="json"), "progress": journey.progress},
        "chapters": [c.model_dump(mode="json") for c in chapters],
        "session_id": session_id,
        "mensagem_sugerida": (
            f"Quero que você seja minha mentora em «{topic}». "
            f"Meu nível é {body.level}. Objetivo: {goal}. "
            f"{first_bit}"
            "Explique com clareza, dê um exemplo e no fim me faça uma pergunta de checagem. "
            "Depois eu anoto o que aprendi e faço o quiz do capítulo."
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


# --------------------------------------------------------------- capítulos
@router.get("/tracks/{track_id}/chapters")
def list_chapters(
    track_id: str,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    if not memory.education.get_track(user_id, track_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "trilha não encontrada")
    return [c.model_dump(mode="json") for c in memory.education.list_chapters(user_id, track_id)]


@router.post("/tracks/{track_id}/chapters/generate")
async def generate_chapters(
    track_id: str,
    user_id: str = Depends(current_user_id),
    mentor: StudyMentor = Depends(get_study_mentor),
):
    try:
        chapters = await mentor.generate_chapters(user_id, track_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return [c.model_dump(mode="json") for c in chapters]


@router.patch("/chapters/{chapter_id}")
def patch_chapter(
    chapter_id: str,
    new_status: str = "concluido",
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    if new_status not in {"pendente", "em_progresso", "concluido"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "status inválido")
    if not memory.education.set_chapter_status(user_id, chapter_id, new_status):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "capítulo não encontrado")
    ch = memory.education.get_chapter(user_id, chapter_id)
    return ch.model_dump(mode="json") if ch else {}


# ------------------------------------------------------------------- quizzes
@router.get("/tracks/{track_id}/quizzes")
def list_quizzes(
    track_id: str,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    return [q.model_dump(mode="json") for q in memory.education.list_quizzes(user_id, track_id)]


@router.post("/tracks/{track_id}/quizzes")
async def create_quiz(
    track_id: str,
    chapter_id: str | None = None,
    user_id: str = Depends(current_user_id),
    mentor: StudyMentor = Depends(get_study_mentor),
):
    try:
        quiz = await mentor.generate_quiz(user_id, track_id, chapter_id=chapter_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return quiz.model_dump(mode="json")


@router.get("/quizzes/{quiz_id}")
def get_quiz(
    quiz_id: str,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    quiz = memory.education.get_quiz(user_id, quiz_id)
    if not quiz:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "quiz não encontrado")
    return quiz.model_dump(mode="json")


@router.post("/quizzes/{quiz_id}/submit")
async def submit_quiz(
    quiz_id: str,
    body: QuizSubmit,
    user_id: str = Depends(current_user_id),
    mentor: StudyMentor = Depends(get_study_mentor),
):
    try:
        quiz = await mentor.grade_quiz(user_id, quiz_id, body.answers)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return quiz.model_dump(mode="json")


# --------------------------------------------------------------- materiais
@router.get("/tracks/{track_id}/materials")
def list_materials(
    track_id: str,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    if not memory.education.get_track(user_id, track_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "trilha não encontrada")
    return [m.model_dump(mode="json") for m in memory.education.list_materials(user_id, track_id)]


@router.post("/tracks/{track_id}/materials", status_code=status.HTTP_202_ACCEPTED)
async def upload_material(
    track_id: str,
    background: BackgroundTasks,
    file: UploadFile,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
    ingestor: DocumentIngestor = Depends(get_ingestor),
    llm: LLM = Depends(get_llm),
):
    """PDF/txt/md da trilha → grafo. PDF escaneado usa OCR (Gemini)."""
    from app.knowledge.extract import extract_document

    track = memory.education.get_track(user_id, track_id)
    if not track:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "trilha não encontrada")

    raw = await file.read()
    if len(raw) > 12_000_000:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "arquivo acima de 12 MB")

    title = file.filename or f"material-{track.title}"
    ocr_fn = llm.ocr if hasattr(llm, "ocr") else None
    try:
        text, fmt = await extract_document(title, raw, ocr=ocr_fn)
    except ValueError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc

    if len(text.strip()) < 40:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "documento sem conteúdo suficiente")

    mat = memory.education.create_material(
        StudyMaterial(
            user_id=user_id,
            track_id=track_id,
            title=title,
            formato=fmt,
            status="processando",
        )
    )

    async def _run() -> None:
        try:
            report = await ingestor.ingest(
                user_id,
                f"[{track.title}] {title}",
                text,
                {"origem": "trilha", "track_id": track_id, "formato": fmt},
            )
            memory.education.update_material(
                user_id, mat.id, node_id=report.document_id, status="pronto"
            )
        except Exception:
            memory.education.update_material(user_id, mat.id, status="erro")

    background.add_task(_run)
    aviso = "Material sendo estruturado no grafo. Em instantes a Ayra poderá usá-lo."
    if fmt == "pdf-ocr":
        aviso = "PDF escaneado lido via OCR. " + aviso
    return {
        **mat.model_dump(mode="json"),
        "caracteres": len(text),
        "aviso": aviso,
    }


# --------------------------------------------------------------- progresso
@router.get("/tracks/{track_id}/progress")
def track_progress(
    track_id: str,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    progress = memory.education.track_progress(user_id, track_id)
    if not progress:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "trilha não encontrada")
    return progress.model_dump(mode="json")


# ----------------------------------------------------------------- simulado
@router.post("/tracks/{track_id}/simulado")
async def create_simulado(
    track_id: str,
    user_id: str = Depends(current_user_id),
    mentor: StudyMentor = Depends(get_study_mentor),
):
    try:
        quiz = await mentor.generate_simulado(user_id, track_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return quiz.model_dump(mode="json")


# ---------------------------------------------------------- revisão espaçada
@router.get("/reviews/due")
def list_due_reviews(
    track_id: str | None = None,
    limit: int = 30,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    cards = memory.education.list_due_reviews(user_id, track_id=track_id, limit=limit)
    return [c.model_dump(mode="json") for c in cards]


@router.post("/reviews/{card_id}/grade")
def grade_review(
    card_id: str,
    body: ReviewGrade,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    updated = memory.education.grade_review(user_id, card_id, body.rating)
    if not updated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cartão não encontrado")
    return updated.model_dump(mode="json")


# ------------------------------------------------------------- plano semanal
@router.get("/weekly-plan")
def get_weekly_plan(
    track_id: str | None = None,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    return memory.education.weekly_plan_status(user_id, track_id=track_id)


@router.put("/weekly-plan")
def put_weekly_plan(
    body: StudyWeeklyPlanCreate,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    if body.track_id and not memory.education.get_track(user_id, body.track_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "trilha não encontrada")
    week = memory.education.monday_of()
    plan = memory.education.upsert_weekly_plan(
        StudyWeeklyPlan(
            user_id=user_id,
            track_id=body.track_id,
            week_start=week,
            target_minutes=body.target_minutes,
            target_sessions=body.target_sessions,
        )
    )
    status_now = memory.education.weekly_plan_status(user_id, track_id=body.track_id)
    return {**status_now, "plan": plan.model_dump(mode="json")}

