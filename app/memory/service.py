"""
MemoryService — a fachada que a Ayra usa. Equivalente ao MemoryManager antigo,
mas com uma responsabilidade que ele não tinha: RECUPERAR contexto, e não apenas
guardar dados.

Guardar é a parte fácil. O produto vive ou morre em `build_context()`: escolher
as 5 coisas certas entre 10 mil para colocar dentro da janela do modelo.
"""

from __future__ import annotations

import logging

from app.cabinet.store import CabinetStore
from app.core.db import Database
from app.domain.models import AyraContext
from app.education.store import EducationStore
from app.finance.store import FinanceStore
from app.llm.base import LLM
from app.memory.store import (
    AuditStore,
    ConversationStore,
    JourneyStore,
    KnowledgeStore,
    PersonalStore,
)

log = logging.getLogger("atlas.memory")

_FINANCE_HINTS = (
    "financ", "dinheiro", "orçamento", "orcamento", "salário", "salario",
    "despesa", "receita", "investir", "investimento", "dívida", "divida",
    "reserva", "patrimônio", "patrimonio", "meta financeira", "poupar",
    "gast", "conta banc", "fluxo de caixa",
)

_EDU_HINTS = (
    "estud", "aprend", "aula", "matéria", "materia", "prova", "concurso",
    "trilha", "competên", "competen", "exercício", "exercicio", "revisar",
    "matemática", "matematica", "física", "fisica", "direito", "medicina",
    "program", "python", "história", "historia", "idioma", "inglês", "ingles",
    "química", "quimica", "biologia", "filosofia",
)

_CABINET_HINTS = (
    "gabinete", "demanda", "município", "municipio", "vereador", "prefeito",
    "parlamentar", "cidadão", "cidadao", "ofício", "oficio", "agenda",
    "mandato", "liderança", "lideranca", "convênio", "convenio", "assessoria",
)


class MemoryService:
    def __init__(self, db: Database, llm: LLM) -> None:
        self.db = db
        self.llm = llm
        self.conversation = ConversationStore(db)
        self.personal = PersonalStore(db)
        self.knowledge = KnowledgeStore(db)
        self.journeys = JourneyStore(db)
        self.finance = FinanceStore(db)
        self.education = EducationStore(db)
        self.cabinet = CabinetStore(db)
        self.audit = AuditStore(db)

    async def build_context(
        self,
        user_id: str,
        session_id: str,
        question: str,
        history_turns: int = 12,
        knowledge_hits: int = 5,
        journey_id: str | None = None,
    ) -> AyraContext:
        history = self.conversation.history(user_id, session_id, limit=history_turns)

        session = self.conversation.get_session(user_id, session_id)
        jid = journey_id or (session.journey_id if session else None)
        journey = self.journeys.get(user_id, jid) if jid else self.journeys.active(user_id)

        personal = []
        for cat in ("objetivos", "preferencias", "restricoes", "contexto", "rotina"):
            personal += self.personal.list(user_id, category=cat, limit=4)

        hits = []
        if self.knowledge.count_nodes(user_id) > 0:
            query_vec = None
            model = None
            try:
                vecs = await self.llm.embed([question])
                if vecs:
                    query_vec, model = vecs[0], self.llm.embedding_model
            except Exception:
                log.warning("embedding da pergunta falhou; usando só busca léxica")

            hits = self.knowledge.search(
                user_id, question, query_embedding=query_vec, model=model, top_k=knowledge_hits
            )

        q = question.lower()
        domain = journey.domain if journey else None

        finance = None
        if any(h in q for h in _FINANCE_HINTS) or domain == "financas":
            if self.finance.list_accounts(user_id):
                finance = self.finance.snapshot(user_id)
                self.audit.log(user_id, "read", "finance_snapshot", "snapshot",
                               session_id, "contexto financeiro")

        education = None
        if any(h in q for h in _EDU_HINTS) or domain == "educacao":
            snap = self.education.snapshot(user_id)
            # Sempre injeta no domínio educação — a mentora precisa do caderno mesmo no dia 1.
            if snap.tracks_ativas or snap.competencias or snap.notas_recentes or domain == "educacao":
                education = snap
                self.audit.log(user_id, "read", "education_snapshot", "snapshot",
                               session_id, "contexto educacional")

        cabinet = None
        if any(h in q for h in _CABINET_HINTS) or domain == "gabinete":
            snap = self.cabinet.snapshot(user_id)
            if snap.demandas_abertas or snap.agenda or snap.municipios:
                cabinet = snap
                self.audit.log(user_id, "read", "cabinet_snapshot", "snapshot",
                               session_id, "contexto de gabinete")

        for m in personal:
            self.audit.log(user_id, "read", "personal_memory", m.id, session_id, "contexto da resposta")
        for h in hits:
            self.audit.log(user_id, "read", "knowledge_node", h.node.id, session_id, "contexto da resposta")
        if journey:
            self.audit.log(user_id, "read", "journey", journey.id, session_id, "jornada da conversa")

        return AyraContext(
            personal=personal, knowledge=hits, journey=journey,
            finance=finance, education=education, cabinet=cabinet, history=history,
        )

    def dashboard(self, user_id: str) -> dict:
        journeys = self.journeys.list(user_id)
        active = [j for j in journeys if j.status == "ativa"]
        active_j = active[0] if active else None
        next_step = None
        if active_j:
            for s in active_j.steps:
                if s.status != "concluida":
                    next_step = {"id": s.id, "title": s.title, "journey_id": active_j.id}
                    break

        edu = self.education.snapshot(user_id)
        cab = self.cabinet.snapshot(user_id)
        health = self.finance.health(user_id)
        memories = self.personal.list(user_id, limit=3)
        alerts: list[dict] = []
        if edu.revisoes_vencidas:
            alerts.append({
                "tipo": "estudos", "nivel": "info",
                "texto": f"{edu.revisoes_vencidas} revisão(ões) vencida(s)",
                "view": "education",
            })
        if cab.demandas_atrasadas:
            alerts.append({
                "tipo": "gabinete", "nivel": "warn",
                "texto": f"{cab.demandas_atrasadas} demanda(s) atrasada(s)",
                "view": "cabinet",
            })
        if cab.demandas_urgentes:
            alerts.append({
                "tipo": "gabinete", "nivel": "warn",
                "texto": f"{cab.demandas_urgentes} demanda(s) urgente(s)",
                "view": "cabinet",
            })
        if health.reserva_meses is not None and health.reserva_meses < 3:
            alerts.append({
                "tipo": "financas", "nivel": "warn",
                "texto": f"Reserva baixa: {health.reserva_meses} mês(es)",
                "view": "finance",
            })
        if health.taxa_poupanca < 0 and health.receita_mes > 0:
            alerts.append({
                "tipo": "financas", "nivel": "warn",
                "texto": "Mês no vermelho (despesa > receita)",
                "view": "finance",
            })
        if cab.proximo_compromisso:
            alerts.append({
                "tipo": "gabinete", "nivel": "info",
                "texto": f"Próximo: {cab.proximo_compromisso.title}",
                "view": "cabinet",
            })

        return {
            "jornadas_ativas": len(active),
            "jornadas_total": len(journeys),
            "jornada_ativa": (
                {**active_j.model_dump(mode="json"), "progress": active_j.progress}
                if active_j else None
            ),
            "proximo_passo": next_step,
            "alertas": alerts,
            "memorias": len(self.personal.list(user_id, limit=10_000)),
            "memorias_recentes": [m.model_dump(mode="json") for m in memories],
            "conhecimento_nos": self.knowledge.count_nodes(user_id),
            "financas": health.model_dump(mode="json"),
            "educacao": {
                "trilhas_ativas": len(edu.tracks_ativas),
                "minutos_semana": edu.minutos_semana,
                "areas": edu.areas,
                "competencias": len(edu.competencias),
                "revisoes_vencidas": edu.revisoes_vencidas,
                "capitulos_pendentes": edu.capitulos_pendentes,
            },
            "gabinete": {
                "demandas_abertas": cab.demandas_abertas,
                "demandas_urgentes": cab.demandas_urgentes,
                "demandas_atrasadas": cab.demandas_atrasadas,
                "municipios": cab.municipios,
                "proximo_compromisso": (
                    cab.proximo_compromisso.model_dump(mode="json")
                    if cab.proximo_compromisso else None
                ),
            },
        }

    def export_all(self, user_id: str) -> dict:
        fin = self.finance.snapshot(user_id)
        edu = self.education.snapshot(user_id)
        cab = self.cabinet.snapshot(user_id)
        return {
            "usuario": user_id,
            "memoria_pessoal": [m.model_dump(mode="json") for m in self.personal.list(user_id, limit=10_000)],
            "jornadas": [j.model_dump(mode="json") for j in self.journeys.list(user_id)],
            "conhecimento": [
                dict(r) for r in self.db.connect().execute(
                    "SELECT id, node_type, title, description, metadata, created_at "
                    "FROM knowledge_nodes WHERE user_id = ?", (user_id,)
                ).fetchall()
            ],
            "relacoes": [
                dict(r) for r in self.db.connect().execute(
                    "SELECT source_id, target_id, rel_type FROM knowledge_edges WHERE user_id = ?",
                    (user_id,),
                ).fetchall()
            ],
            "financas": {
                "contas": [a.model_dump(mode="json") for a in fin.contas],
                "metas": [{**g.model_dump(mode="json"), "progress": g.progress} for g in fin.metas],
                "saude": fin.health.model_dump(mode="json"),
                "lancamentos": [t.model_dump(mode="json") for t in self.finance.list_transactions(user_id, limit=10_000)],
            },
            "educacao": edu.model_dump(mode="json"),
            "gabinete": {
                "snapshot": cab.model_dump(mode="json"),
                "cidadaos": [c.model_dump(mode="json") for c in self.cabinet.list_citizens(user_id)],
                "demandas": [d.model_dump(mode="json") for d in self.cabinet.list_demands(user_id, limit=10_000)],
                "timeline": [e.model_dump(mode="json") for e in self.cabinet.list_timeline(user_id, limit=10_000)],
                "agenda": [a.model_dump(mode="json") for a in self.cabinet.list_agenda(user_id, limit=10_000)],
            },
        }

    def wipe(self, user_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        tables = [
            "conversational_turns", "sessions", "personal_memories",
            "knowledge_edges", "knowledge_nodes", "embeddings",
            "journey_steps", "journeys", "projects", "memory_access_log",
            "finance_transactions", "finance_goals", "finance_accounts",
            "study_sessions", "study_notes", "study_quizzes", "study_chapters",
            "study_materials", "study_review_cards", "study_weekly_plans",
            "competencies", "study_tracks",
            "cabinet_timeline", "cabinet_agenda", "cabinet_demands", "cabinet_citizens",
        ]
        with self.db.tx() as c:
            for t in tables:
                counts[t] = c.execute(f"DELETE FROM {t} WHERE user_id = ?", (user_id,)).rowcount
        return counts
