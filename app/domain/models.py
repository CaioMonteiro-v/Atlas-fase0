"""
Modelos de domínio do Atlas.

Substitui as classes manuais com to_dict()/from_dict(). Pydantic v2 dá de graça:
validação, serialização, schema OpenAPI e parsing de JSON do LLM.

Regra importante: `user_id` NÃO aparece nos modelos de entrada da API (*Create).
Ele vem sempre da autenticação, nunca do corpo da requisição.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    """Timestamp timezone-aware. datetime.now() sem tz é uma bomba-relógio."""
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class Privacy(str, Enum):
    """Cap. 126 — privacidade por padrão. O default de tudo é PRIVATE."""

    PUBLIC = "public"
    PRIVATE = "private"
    RESTRICTED = "restricted"  # conhecimento estruturado do sistema
    EPHEMERAL = "ephemeral"    # descartável ao fim da sessão


class AtlasModel(BaseModel):
    model_config = ConfigDict(use_enum_values=False, from_attributes=True)


# --------------------------------------------------------------------------
# Camada 1 — Conversacional
# --------------------------------------------------------------------------
class Turn(AtlasModel):
    id: str = Field(default_factory=new_id)
    user_id: str
    session_id: str
    turn_number: int
    speaker: Literal["user", "ayra", "system"]
    text: str
    created_at: datetime = Field(default_factory=utcnow)


class Session(AtlasModel):
    id: str = Field(default_factory=new_id)
    user_id: str
    journey_id: str | None = None
    title: str | None = None
    consolidated_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------
# Camada 2 — Pessoal
# --------------------------------------------------------------------------
class PersonalMemory(AtlasModel):
    id: str = Field(default_factory=new_id)
    user_id: str
    category: str                       # objetivos | preferencias | rotina | contexto ...
    content: dict[str, Any]
    tags: list[str] = Field(default_factory=list)
    privacy: Privacy = Privacy.PRIVATE
    confidence: float = 1.0             # 1.0 = o usuário afirmou; < 1.0 = a Ayra inferiu
    source_session_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PersonalMemoryCreate(BaseModel):
    category: str
    content: dict[str, Any]
    tags: list[str] = Field(default_factory=list)
    privacy: Privacy = Privacy.PRIVATE


class PersonalMemoryUpdate(BaseModel):
    category: str | None = None
    content: dict[str, Any] | None = None
    tags: list[str] | None = None
    privacy: Privacy | None = None


# --------------------------------------------------------------------------
# Camada 3 — Conhecimento (grafo)
# --------------------------------------------------------------------------
NodeType = Literal["Documento", "Conceito", "Aplicacao", "Exercicio"]


class KnowledgeNode(AtlasModel):
    id: str = Field(default_factory=new_id)
    user_id: str
    node_type: NodeType
    title: str
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    privacy: Privacy = Privacy.RESTRICTED
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class KnowledgeEdge(AtlasModel):
    id: str = Field(default_factory=new_id)
    user_id: str
    source_id: str
    target_id: str
    rel_type: str  # EXPLICA | REQUER | APLICA | CONTRASTA | CONTEM_CONCEITO ...
    properties: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class SearchHit(AtlasModel):
    """Resultado de busca com score — o que a Ayra injeta no contexto."""

    node: KnowledgeNode
    score: float
    matched_by: Literal["vector", "keyword", "hybrid"]


# --- Schema de extração usado pelo LLM (structured output) ---
class ExtractedConcept(BaseModel):
    nome: str
    definicao: str
    palavras_chave: list[str] = Field(default_factory=list)


class ExtractedRelation(BaseModel):
    origem: str
    destino: str
    tipo: Literal["EXPLICA", "APLICA", "REQUER", "CONTRASTA"]
    descricao: str = ""


class ExtractedApplication(BaseModel):
    conceito_base: str
    exemplo_uso: str
    contexto: str = ""


class ExtractedExercise(BaseModel):
    conceito_base: str
    pergunta: str
    resposta_esperada: str = ""
    dificuldade: Literal["FACIL", "MEDIO", "DIFICIL"] = "MEDIO"


class Extraction(BaseModel):
    """O contrato de saída do LLM na ingestão. Se o LLM devolver lixo,
    o Pydantic rejeita aqui — e não 3 camadas abaixo, no banco."""

    conceitos: list[ExtractedConcept] = Field(default_factory=list)
    relacoes: list[ExtractedRelation] = Field(default_factory=list)
    aplicacoes: list[ExtractedApplication] = Field(default_factory=list)
    exercicios: list[ExtractedExercise] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Camada 4 — Jornadas (a unidade central do Atlas, Cap. 19)
# --------------------------------------------------------------------------
JourneyStatus = Literal[
    "descobrindo", "diagnosticando", "ativa", "pausada", "concluida", "abandonada"
]


class JourneyStep(AtlasModel):
    id: str = Field(default_factory=new_id)
    journey_id: str
    user_id: str
    order_index: int
    title: str
    description: str = ""
    status: Literal["pendente", "em_progresso", "concluida", "pulada"] = "pendente"
    due_date: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Journey(AtlasModel):
    id: str = Field(default_factory=new_id)
    user_id: str
    project_id: str | None = None
    domain: str = "geral"
    title: str
    stated_goal: str = ""   # Etapa 1 — o que o usuário pediu
    real_goal: str = ""     # Etapa 1 — o que ele realmente quer
    diagnosis: dict[str, Any] = Field(default_factory=dict)  # Etapa 2
    status: JourneyStatus = "descobrindo"
    privacy: Privacy = Privacy.PRIVATE
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    steps: list[JourneyStep] = Field(default_factory=list)

    @property
    def progress(self) -> float:
        if not self.steps:
            return 0.0
        done = sum(1 for s in self.steps if s.status == "concluida")
        return round(done / len(self.steps), 2)


class JourneyCreate(BaseModel):
    title: str
    stated_goal: str = ""
    domain: str = "geral"
    project_id: str | None = None


class Project(AtlasModel):
    id: str = Field(default_factory=new_id)
    user_id: str
    name: str
    description: str = ""
    status: str = "ativo"
    privacy: Privacy = Privacy.PRIVATE
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------
# Contexto que a Ayra monta antes de falar (Cap. 115 — ponto único de entrada)
# --------------------------------------------------------------------------
class AyraContext(AtlasModel):
    personal: list[PersonalMemory] = Field(default_factory=list)
    knowledge: list[SearchHit] = Field(default_factory=list)
    journey: Journey | None = None
    history: list[Turn] = Field(default_factory=list)

    def sources(self) -> list[dict[str, str]]:
        """Cap. 127 — o que a Ayra usou. Vai junto na resposta, sempre."""
        out: list[dict[str, str]] = []
        out += [{"tipo": "memoria_pessoal", "id": m.id, "rotulo": m.category} for m in self.personal]
        out += [{"tipo": "conhecimento", "id": h.node.id, "rotulo": h.node.title} for h in self.knowledge]
        if self.journey:
            out.append({"tipo": "jornada", "id": self.journey.id, "rotulo": self.journey.title})
        return out
