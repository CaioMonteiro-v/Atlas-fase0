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
# Domínio Financeiro (Cap. 82–92)
# --------------------------------------------------------------------------
FinanceAccountKind = Literal["corrente", "poupanca", "investimento", "carteira", "cartao", "outro"]
FinanceTxKind = Literal["receita", "despesa", "transferencia"]
FinanceGoalStatus = Literal["ativa", "concluida", "pausada", "abandonada"]


class FinanceAccount(AtlasModel):
    id: str = Field(default_factory=new_id)
    user_id: str
    name: str
    kind: FinanceAccountKind = "corrente"
    currency: str = "BRL"
    balance: float = 0.0
    privacy: Privacy = Privacy.PRIVATE
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class FinanceAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: FinanceAccountKind = "corrente"
    currency: str = "BRL"
    balance: float = 0.0


class FinanceTransaction(AtlasModel):
    id: str = Field(default_factory=new_id)
    user_id: str
    account_id: str
    kind: FinanceTxKind
    amount: float = Field(gt=0)
    category: str = "geral"
    description: str = ""
    occurred_at: datetime = Field(default_factory=utcnow)
    created_at: datetime = Field(default_factory=utcnow)


class FinanceTransactionCreate(BaseModel):
    account_id: str
    kind: FinanceTxKind
    amount: float = Field(gt=0)
    category: str = "geral"
    description: str = ""
    occurred_at: datetime | None = None


class FinanceGoal(AtlasModel):
    id: str = Field(default_factory=new_id)
    user_id: str
    journey_id: str | None = None
    title: str
    target_amount: float = Field(gt=0)
    current_amount: float = 0.0
    deadline: datetime | None = None
    status: FinanceGoalStatus = "ativa"
    privacy: Privacy = Privacy.PRIVATE
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def progress(self) -> float:
        if self.target_amount <= 0:
            return 0.0
        return round(min(1.0, self.current_amount / self.target_amount), 2)


class FinanceGoalCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    target_amount: float = Field(gt=0)
    current_amount: float = 0.0
    deadline: datetime | None = None
    journey_id: str | None = None


class FinanceHealth(AtlasModel):
    """Cap. 92 — indicadores de saúde financeira (calculados, não persistidos)."""

    patrimonio: float = 0.0
    receita_mes: float = 0.0
    despesa_mes: float = 0.0
    poupanca_mes: float = 0.0
    taxa_poupanca: float = 0.0          # 0..1
    comprometimento: float = 0.0        # despesa / receita, 0..n
    reserva_meses: float | None = None  # patrimônio líquido / despesa média
    metas_ativas: int = 0
    progresso_metas: float = 0.0


class FinanceSnapshot(AtlasModel):
    """Resumo que a Ayra injeta no contexto quando o domínio financeiro importa."""

    health: FinanceHealth
    contas: list[FinanceAccount] = Field(default_factory=list)
    metas: list[FinanceGoal] = Field(default_factory=list)
    recentes: list[FinanceTransaction] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Domínio Educação (Cap. 69–81) — estudo GERAL
# Idiomas são uma subject_area, não o domínio inteiro.
# --------------------------------------------------------------------------
StudyLevel = Literal["iniciante", "intermediario", "avancado"]
StudyTrackStatus = Literal["ativa", "pausada", "concluida", "abandonada"]
CompetencyLevel = Literal["iniciar", "praticar", "proficiente", "dominio"]
CompetencyStatus = Literal["em_desenvolvimento", "adquirida", "a_revisar"]

# Áreas canônicas — abertas o suficiente para qualquer assunto.
SUBJECT_AREAS = (
    "geral", "matematica", "fisica", "quimica", "biologia", "medicina",
    "direito", "historia", "filosofia", "administracao", "economia",
    "programacao", "engenharia", "inteligencia_artificial", "idiomas",
    "musica", "artes", "concursos", "outro",
)


class StudyTrack(AtlasModel):
    id: str = Field(default_factory=new_id)
    user_id: str
    journey_id: str | None = None
    title: str
    subject_area: str = "geral"
    level: StudyLevel = "iniciante"
    goal: str = ""
    status: StudyTrackStatus = "ativa"
    privacy: Privacy = Privacy.PRIVATE
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class StudyTrackCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    subject_area: str = "geral"
    level: StudyLevel = "iniciante"
    goal: str = ""
    journey_id: str | None = None


class StudySession(AtlasModel):
    id: str = Field(default_factory=new_id)
    user_id: str
    track_id: str
    minutes: int = Field(gt=0)
    notes: str = ""
    topics: list[str] = Field(default_factory=list)
    occurred_at: datetime = Field(default_factory=utcnow)
    created_at: datetime = Field(default_factory=utcnow)


class StudySessionCreate(BaseModel):
    track_id: str
    minutes: int = Field(gt=0, le=24 * 60)
    notes: str = ""
    topics: list[str] = Field(default_factory=list)
    occurred_at: datetime | None = None


class Competency(AtlasModel):
    id: str = Field(default_factory=new_id)
    user_id: str
    track_id: str | None = None
    name: str
    subject_area: str = "geral"
    level: CompetencyLevel = "iniciar"
    evidence: str = ""
    status: CompetencyStatus = "em_desenvolvimento"
    privacy: Privacy = Privacy.PRIVATE
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class CompetencyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    subject_area: str = "geral"
    level: CompetencyLevel = "iniciar"
    evidence: str = ""
    track_id: str | None = None


class EducationSnapshot(AtlasModel):
    tracks_ativas: list[StudyTrack] = Field(default_factory=list)
    sessoes_recentes: list[StudySession] = Field(default_factory=list)
    competencias: list[Competency] = Field(default_factory=list)
    minutos_semana: int = 0
    areas: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Domínio Gabinete Inteligente (Cap. 97–105)
# --------------------------------------------------------------------------
DemandPriority = Literal["baixa", "media", "alta", "urgente"]
DemandStatus = Literal["aberta", "em_andamento", "aguardando", "concluida", "arquivada"]
TimelineEventType = Literal["contato", "demanda", "documento", "visita", "reuniao", "retorno", "nota"]
AgendaStatus = Literal["agendado", "realizado", "cancelado"]


class CabinetCitizen(AtlasModel):
    id: str = Field(default_factory=new_id)
    user_id: str
    name: str
    municipality: str = ""
    contact: str = ""
    notes: str = ""
    privacy: Privacy = Privacy.PRIVATE
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class CabinetCitizenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    municipality: str = ""
    contact: str = ""
    notes: str = ""


class CabinetDemand(AtlasModel):
    id: str = Field(default_factory=new_id)
    user_id: str
    citizen_id: str | None = None
    title: str
    subject: str = ""
    municipality: str = ""
    category: str = "geral"
    priority: DemandPriority = "media"
    status: DemandStatus = "aberta"
    origin: str = ""
    assignee: str = ""
    due_date: datetime | None = None
    result: str = ""
    privacy: Privacy = Privacy.PRIVATE
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class CabinetDemandCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    subject: str = ""
    municipality: str = ""
    category: str = "geral"
    priority: DemandPriority = "media"
    citizen_id: str | None = None
    origin: str = ""
    assignee: str = ""
    due_date: datetime | None = None


class CabinetDemandUpdate(BaseModel):
    status: DemandStatus | None = None
    priority: DemandPriority | None = None
    assignee: str | None = None
    result: str | None = None
    subject: str | None = None


class CabinetTimelineEvent(AtlasModel):
    id: str = Field(default_factory=new_id)
    user_id: str
    citizen_id: str | None = None
    demand_id: str | None = None
    municipality: str = ""
    event_type: TimelineEventType = "nota"
    title: str
    description: str = ""
    occurred_at: datetime = Field(default_factory=utcnow)
    created_at: datetime = Field(default_factory=utcnow)


class CabinetTimelineCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    event_type: TimelineEventType = "nota"
    description: str = ""
    citizen_id: str | None = None
    demand_id: str | None = None
    municipality: str = ""
    occurred_at: datetime | None = None


class CabinetAgendaItem(AtlasModel):
    id: str = Field(default_factory=new_id)
    user_id: str
    title: str
    municipality: str = ""
    related_demand_id: str | None = None
    starts_at: datetime
    notes: str = ""
    status: AgendaStatus = "agendado"
    privacy: Privacy = Privacy.PRIVATE
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class CabinetAgendaCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    starts_at: datetime
    municipality: str = ""
    related_demand_id: str | None = None
    notes: str = ""


class CabinetSnapshot(AtlasModel):
    demandas_abertas: int = 0
    demandas_urgentes: int = 0
    municipios: list[str] = Field(default_factory=list)
    recentes: list[CabinetDemand] = Field(default_factory=list)
    agenda: list[CabinetAgendaItem] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Planejamento de jornada pela Ayra (Cap. 20, Etapa 3)
# --------------------------------------------------------------------------
class PlannedStep(BaseModel):
    title: str
    description: str = ""


class JourneyPlan(BaseModel):
    real_goal: str
    diagnosis: dict[str, Any] = Field(default_factory=dict)
    steps: list[PlannedStep] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Contexto que a Ayra monta antes de falar (Cap. 115 — ponto único de entrada)
# --------------------------------------------------------------------------
class AyraContext(AtlasModel):
    personal: list[PersonalMemory] = Field(default_factory=list)
    knowledge: list[SearchHit] = Field(default_factory=list)
    journey: Journey | None = None
    finance: FinanceSnapshot | None = None
    education: EducationSnapshot | None = None
    cabinet: CabinetSnapshot | None = None
    history: list[Turn] = Field(default_factory=list)

    def sources(self) -> list[dict[str, str]]:
        """Cap. 127 — o que a Ayra usou. Vai junto na resposta, sempre."""
        out: list[dict[str, str]] = []
        out += [{"tipo": "memoria_pessoal", "id": m.id, "rotulo": m.category} for m in self.personal]
        out += [{"tipo": "conhecimento", "id": h.node.id, "rotulo": h.node.title} for h in self.knowledge]
        if self.journey:
            out.append({"tipo": "jornada", "id": self.journey.id, "rotulo": self.journey.title})
        if self.finance:
            out.append({
                "tipo": "financas",
                "id": "snapshot",
                "rotulo": f"patrimônio R$ {self.finance.health.patrimonio:,.2f}",
            })
        if self.education:
            areas = ", ".join(self.education.areas[:3]) or "estudos"
            out.append({"tipo": "educacao", "id": "snapshot", "rotulo": areas})
        if self.cabinet:
            out.append({
                "tipo": "gabinete",
                "id": "snapshot",
                "rotulo": f"{self.cabinet.demandas_abertas} demandas abertas",
            })
        return out
