"""
MemoryService — a fachada que a Ayra usa. Equivalente ao MemoryManager antigo,
mas com uma responsabilidade que ele não tinha: RECUPERAR contexto, e não apenas
guardar dados.

Guardar é a parte fácil. O produto vive ou morre em `build_context()`: escolher
as 5 coisas certas entre 10 mil para colocar dentro da janela do modelo.
"""

from __future__ import annotations

import logging

from app.core.db import Database
from app.domain.models import AyraContext
from app.llm.base import LLM
from app.memory.store import (
    AuditStore,
    ConversationStore,
    JourneyStore,
    KnowledgeStore,
    PersonalStore,
)

log = logging.getLogger("atlas.memory")


class MemoryService:
    def __init__(self, db: Database, llm: LLM) -> None:
        self.db = db
        self.llm = llm
        self.conversation = ConversationStore(db)
        self.personal = PersonalStore(db)
        self.knowledge = KnowledgeStore(db)
        self.journeys = JourneyStore(db)
        self.audit = AuditStore(db)

    async def build_context(
        self,
        user_id: str,
        session_id: str,
        question: str,
        history_turns: int = 12,
        knowledge_hits: int = 5,
    ) -> AyraContext:
        history = self.conversation.history(user_id, session_id, limit=history_turns)
        journey = self.journeys.active(user_id)

        # Memória pessoal: as categorias que quase sempre importam. Mandar TUDO
        # para o modelo é o erro clássico — enche a janela e piora a resposta.
        personal = []
        for cat in ("objetivos", "preferencias", "restricoes", "contexto", "rotina"):
            personal += self.personal.list(user_id, category=cat, limit=4)

        # Conhecimento: busca híbrida.
        #
        # Se o grafo está vazio, embutir a pergunta é jogar uma chamada de API
        # fora — não há nada com que comparar o vetor. Este é o caso enquanto
        # você não ingeriu nenhum documento, ou seja: sempre, no começo.
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

        for m in personal:
            self.audit.log(user_id, "read", "personal_memory", m.id, session_id, "contexto da resposta")
        for h in hits:
            self.audit.log(user_id, "read", "knowledge_node", h.node.id, session_id, "contexto da resposta")

        return AyraContext(personal=personal, knowledge=hits, journey=journey, history=history)

    # ---------------------------------------------------------- Cap. 31 / 128
    def export_all(self, user_id: str) -> dict:
        """A memória pertence ao usuário. Exportar tem que ser trivial —
        e o formato tem que ser legível fora do Atlas."""
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
        }

    def wipe(self, user_id: str) -> dict[str, int]:
        """Apagar tudo. Sem pegadinha, sem 'soft delete', sem resíduo."""
        counts: dict[str, int] = {}
        tables = [
            "conversational_turns", "sessions", "personal_memories",
            "knowledge_edges", "knowledge_nodes", "embeddings",
            "journey_steps", "journeys", "projects", "memory_access_log",
        ]
        with self.db.tx() as c:
            for t in tables:
                counts[t] = c.execute(f"DELETE FROM {t} WHERE user_id = ?", (user_id,)).rowcount
        return counts
