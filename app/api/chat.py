"""
Chat — o único endpoint que o usuário realmente "usa" (Cap. 115).

Streaming via SSE. Escolha deliberada sobre WebSocket: SSE é HTTP puro,
reconecta sozinho, atravessa qualquer proxy e é unidirecional — que é
exatamente a forma do problema (o usuário manda pouco, o modelo devolve muito).
WebSocket seria complexidade sem benefício aqui (Cap. 113 — simplicidade).
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import get_ayra, get_consolidator, get_memory, get_planner
from app.ayra.orchestrator import Ayra, Consolidator, JourneyPlanner
from app.core.security import current_user_id
from app.domain.models import Journey, PersonalMemory, Privacy, new_id
from app.memory.service import MemoryService

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: str | None = None
    journey_id: str | None = None  # amarra a conversa a uma jornada (Cap. 22)
    # Nota: NÃO existe user_id aqui. Ele vem do token. Nunca do cliente.


def _sse(event: str, data: str) -> str:
    payload = data.replace("\r", "")
    lines = "".join(f"data: {line}\n" for line in payload.split("\n"))
    return f"event: {event}\n{lines}\n"


@router.post("")
async def chat(
    body: ChatRequest,
    background: BackgroundTasks,
    user_id: str = Depends(current_user_id),
    ayra: Ayra = Depends(get_ayra),
    consolidator: Consolidator = Depends(get_consolidator),
    memory: MemoryService = Depends(get_memory),
):
    session_id = body.session_id or new_id()
    journey_id = body.journey_id
    if journey_id and not memory.journeys.get(user_id, journey_id):
        journey_id = None

    async def event_stream():
        yield _sse("session", session_id)
        async for kind, payload in ayra.answer(
            user_id, session_id, body.message, journey_id=journey_id
        ):
            yield _sse(kind, payload)

    # A consolidação roda DEPOIS da resposta chegar ao usuário. O usuário nunca
    # espera pela memória: ele espera pela resposta.
    background.add_task(consolidator.run, user_id, session_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx: não bufferize o stream
            "Connection": "keep-alive",
        },
    )


@router.get("/{session_id}/history")
def history(
    session_id: str,
    limit: int = 50,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    turns = memory.conversation.history(user_id, session_id, limit=limit)
    session = memory.conversation.get_session(user_id, session_id)
    return {
        "session_id": session_id,
        "journey_id": session.journey_id if session else None,
        "turns": [t.model_dump(mode="json") for t in turns],
    }


@router.post("/{session_id}/close")
async def close_session(
    session_id: str,
    user_id: str = Depends(current_user_id),
    consolidator: Consolidator = Depends(get_consolidator),
):
    """Encerrar a conversa força a consolidação, sem esperar o múltiplo de N.
    O front chama isto quando o usuário abre uma sessão nova."""
    saved = await consolidator.run(user_id, session_id, force=True)
    return {"fatos_salvos": saved}


@router.delete("/{session_id}")
def forget_session(
    session_id: str,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    """Cap. 30: a memória conversacional pode ser descartada ao fim da conversa."""
    removed = memory.conversation.purge_session(user_id, session_id)
    return {"removed_turns": removed}


# --------------------------------------------------------------------------
# Onboarding — objetivo → jornada planejada em um passo (Cap. 18–20)
# --------------------------------------------------------------------------
class OnboardRequest(BaseModel):
    goal: str = Field(min_length=3, max_length=500)
    domain: str = "geral"
    title: str | None = None


@router.post("/onboard")
async def onboard(
    body: OnboardRequest,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
    planner: JourneyPlanner = Depends(get_planner),
):
    """60 segundos: o usuário diz um objetivo; sai com jornada + plano + sessão."""
    title = (body.title or body.goal).strip()[:120]
    domain = body.domain if body.domain in {"geral", "educacao", "financas", "gabinete"} else "geral"

    # Memória pessoal: o objetivo declarado vira fato durável (confiança alta).
    memory.personal.create(
        PersonalMemory(
            user_id=user_id,
            category="objetivos",
            content={"texto": body.goal.strip()},
            privacy=Privacy.PRIVATE,
            confidence=1.0,
            tags=["onboarding"],
        )
    )

    journey = memory.journeys.create(
        Journey(
            user_id=user_id,
            domain=domain,
            title=title,
            stated_goal=body.goal.strip(),
            status="descobrindo",
        )
    )
    planned = await planner.plan(user_id, journey.id)
    journey = planned or memory.journeys.get(user_id, journey.id)

    session_id = new_id()
    memory.conversation.ensure_session(user_id, session_id, journey_id=journey.id)

    return {
        "session_id": session_id,
        "journey": {**journey.model_dump(mode="json"), "progress": journey.progress},
        "mensagem_sugerida": (
            f"Acabei de começar a jornada «{journey.title}». "
            f"Meu objetivo é: {journey.real_goal or journey.stated_goal}. "
            "Por onde começamos no primeiro passo?"
        ),
    }
