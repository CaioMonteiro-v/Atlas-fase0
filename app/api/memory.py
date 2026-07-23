"""
Memória — os endpoints que tornam os Cap. 31, 127 e 128 executáveis.

"A memória pertence ao usuário" é uma frase bonita no PDF. Só vira verdade
quando existem estas rotas: ver, editar, remover, exportar, apagar tudo, e
auditar o que a Ayra leu. Sem elas, a frase é marketing.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from app.api.deps import get_memory
from app.core.security import current_user_id
from app.domain.models import (
    PersonalMemory,
    PersonalMemoryCreate,
    PersonalMemoryUpdate,
    Privacy,
)
from app.memory.service import MemoryService

router = APIRouter(prefix="/memory", tags=["memoria"])


@router.get("/personal")
def list_personal(
    category: str | None = None,
    privacy: Privacy | None = None,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    items = memory.personal.list(user_id, category=category, privacy=privacy)
    return [m.model_dump(mode="json") for m in items]


@router.post("/personal", status_code=status.HTTP_201_CREATED)
def create_personal(
    body: PersonalMemoryCreate,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    mem = memory.personal.create(PersonalMemory(user_id=user_id, **body.model_dump()))
    memory.audit.log(user_id, "write", "personal_memory", mem.id, reason="criada pelo usuário")
    return mem.model_dump(mode="json")


@router.patch("/personal/{mem_id}")
def update_personal(
    mem_id: str,
    body: PersonalMemoryUpdate,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    patch = body.model_dump(exclude_none=True)
    if not patch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "nada para atualizar")
    updated = memory.personal.update(user_id, mem_id, patch)
    if not updated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "memória não encontrada")
    memory.audit.log(user_id, "write", "personal_memory", mem_id, reason="editada pelo usuário")
    return updated.model_dump(mode="json")


@router.delete("/personal/{mem_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_personal(
    mem_id: str,
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    if not memory.personal.delete(user_id, mem_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "memória não encontrada")
    memory.audit.log(user_id, "delete", "personal_memory", mem_id, reason="removida pelo usuário")


@router.get("/audit")
def audit(
    limit: int = Query(100, le=1000),
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    """Cap. 127 — o que a Ayra leu, quando e por quê. Fim da caixa-preta."""
    return memory.audit.recent(user_id, limit)


@router.get("/export")
def export(
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    """Portabilidade real: JSON completo, legível fora do Atlas.

    Este endpoint é o núcleo do diferencial do projeto. A memória é portátil e
    do usuário — ele pode sair levando tudo. É justamente isso que torna razoável
    confiar o resto a você."""
    data = memory.export_all(user_id)
    return JSONResponse(
        content=data,
        headers={"Content-Disposition": 'attachment; filename="atlas-memoria.json"'},
    )


@router.post("/wipe")
def wipe(
    confirm: str = Query(..., description="digite APAGAR TUDO para confirmar"),
    user_id: str = Depends(current_user_id),
    memory: MemoryService = Depends(get_memory),
):
    if confirm != "APAGAR TUDO":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "confirmação inválida")
    return memory.wipe(user_id)
