"""
Chat — o único endpoint que o usuário realmente "usa" (Cap. 115).

Streaming via SSE. Escolha deliberada sobre WebSocket: SSE é HTTP puro,
reconecta sozinho, atravessa qualquer proxy e é unidirecional — que é
exatamente a forma do problema (o usuário manda pouco, o modelo devolve muito).
WebSocket seria complexidade sem benefício aqui (Cap. 113 — simplicidade).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import get_ayra, get_consolidator, get_memory
from app.ayra.orchestrator import Ayra, Consolidator
from app.core.security import current_user_id
from app.domain.models import new_id
from app.memory.service import MemoryService

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: str | None = None
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
):
    session_id = body.session_id or new_id()

    async def event_stream():
        yield _sse("session", session_id)
        async for kind, payload in ayra.answer(user_id, session_id, body.message):
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
    return {"session_id": session_id, "turns": [t.model_dump(mode="json") for t in turns]}


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
