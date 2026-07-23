"""
Store SQLite — implementação concreta das 4 camadas de memória.

Regra invariável: TODA query filtra por user_id. Não existe caminho de leitura
sem tenant. Isolamento por usuário é aplicado aqui, na borda do banco, e não
confiado à camada de cima.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from app.core.db import Database
from app.domain.models import (
    Journey,
    JourneyStep,
    KnowledgeEdge,
    KnowledgeNode,
    PersonalMemory,
    Privacy,
    SearchHit,
    Session,
    Turn,
    new_id,
    utcnow,
)
from app.memory import vector

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


# --------------------------------------------------------------------------
# Camada 1 — Conversacional
# --------------------------------------------------------------------------
class ConversationStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def ensure_session(self, user_id: str, session_id: str, journey_id: str | None = None) -> Session:
        now = utcnow()
        with self.db.tx() as c:
            row = c.execute(
                "SELECT * FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id)
            ).fetchone()
            if row:
                return self._to_session(row)
            c.execute(
                """INSERT INTO sessions (id, user_id, journey_id, title, created_at, updated_at)
                   VALUES (?, ?, ?, NULL, ?, ?)""",
                (session_id, user_id, journey_id, _iso(now), _iso(now)),
            )
        return Session(id=session_id, user_id=user_id, journey_id=journey_id, created_at=now, updated_at=now)

    def add_turn(self, user_id: str, session_id: str, speaker: str, text: str) -> Turn:
        with self.db.tx() as c:
            n = c.execute(
                "SELECT COALESCE(MAX(turn_number), 0) + 1 AS n FROM conversational_turns WHERE session_id = ?",
                (session_id,),
            ).fetchone()["n"]
            turn = Turn(user_id=user_id, session_id=session_id, turn_number=n, speaker=speaker, text=text)
            c.execute(
                """INSERT INTO conversational_turns
                   (id, user_id, session_id, turn_number, speaker, text, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (turn.id, user_id, session_id, n, speaker, text, _iso(turn.created_at)),
            )
            c.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ? AND user_id = ?",
                (_iso(turn.created_at), session_id, user_id),
            )
        return turn

    def history(self, user_id: str, session_id: str, limit: int = 20) -> list[Turn]:
        """Últimos N turnos, em ordem cronológica.

        O código antigo fazia sorted(...)[:limit] — isso pega os N PRIMEIROS
        turnos da conversa, ou seja, o contexto mais VELHO. Numa conversa longa
        a Ayra estaria lendo o começo e ignorando o que acabou de ser dito.
        """
        rows = self.db.connect().execute(
            """SELECT * FROM conversational_turns
               WHERE user_id = ? AND session_id = ?
               ORDER BY turn_number DESC LIMIT ?""",
            (user_id, session_id, limit),
        ).fetchall()
        return [self._to_turn(r) for r in reversed(rows)]

    def purge_session(self, user_id: str, session_id: str) -> int:
        with self.db.tx() as c:
            cur = c.execute(
                "DELETE FROM conversational_turns WHERE user_id = ? AND session_id = ?",
                (user_id, session_id),
            )
            return cur.rowcount

    @staticmethod
    def _to_turn(r: sqlite3.Row) -> Turn:
        return Turn(
            id=r["id"], user_id=r["user_id"], session_id=r["session_id"],
            turn_number=r["turn_number"], speaker=r["speaker"], text=r["text"],
            created_at=_dt(r["created_at"]),
        )

    @staticmethod
    def _to_session(r: sqlite3.Row) -> Session:
        return Session(
            id=r["id"], user_id=r["user_id"], journey_id=r["journey_id"], title=r["title"],
            consolidated_at=_dt(r["consolidated_at"]),
            created_at=_dt(r["created_at"]), updated_at=_dt(r["updated_at"]),
        )


# --------------------------------------------------------------------------
# Camada 2 — Pessoal
# --------------------------------------------------------------------------
class PersonalStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, mem: PersonalMemory) -> PersonalMemory:
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO personal_memories
                   (id, user_id, category, content, tags, privacy, confidence,
                    source_session_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (mem.id, mem.user_id, mem.category, _json(mem.content), _json(mem.tags),
                 mem.privacy.value, mem.confidence, mem.source_session_id,
                 _iso(mem.created_at), _iso(mem.updated_at)),
            )
        return mem

    def get(self, user_id: str, mem_id: str) -> PersonalMemory | None:
        r = self.db.connect().execute(
            "SELECT * FROM personal_memories WHERE id = ? AND user_id = ?", (mem_id, user_id)
        ).fetchone()
        return self._to_model(r) if r else None

    def update(self, user_id: str, mem_id: str, patch: dict[str, Any]) -> PersonalMemory | None:
        current = self.get(user_id, mem_id)
        if not current:
            return None
        merged = current.model_copy(update={**patch, "updated_at": utcnow()})
        with self.db.tx() as c:
            c.execute(
                """UPDATE personal_memories
                   SET category = ?, content = ?, tags = ?, privacy = ?, updated_at = ?
                   WHERE id = ? AND user_id = ?""",
                (merged.category, _json(merged.content), _json(merged.tags),
                 merged.privacy.value, _iso(merged.updated_at), mem_id, user_id),
            )
        return merged

    def delete(self, user_id: str, mem_id: str) -> bool:
        with self.db.tx() as c:
            cur = c.execute(
                "DELETE FROM personal_memories WHERE id = ? AND user_id = ?", (mem_id, user_id)
            )
            c.execute(
                "DELETE FROM embeddings WHERE owner_type = 'personal_memory' AND owner_id = ? AND user_id = ?",
                (mem_id, user_id),
            )
            return cur.rowcount > 0

    def list(
        self, user_id: str, category: str | None = None, privacy: Privacy | None = None, limit: int = 200
    ) -> list[PersonalMemory]:
        sql = "SELECT * FROM personal_memories WHERE user_id = ?"
        params: list[Any] = [user_id]
        if category:
            sql += " AND category = ?"
            params.append(category)
        if privacy:
            sql += " AND privacy = ?"
            params.append(privacy.value)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self.db.connect().execute(sql, params).fetchall()
        return [self._to_model(r) for r in rows]

    @staticmethod
    def _to_model(r: sqlite3.Row) -> PersonalMemory:
        return PersonalMemory(
            id=r["id"], user_id=r["user_id"], category=r["category"],
            content=json.loads(r["content"]), tags=json.loads(r["tags"]),
            privacy=Privacy(r["privacy"]), confidence=r["confidence"],
            source_session_id=r["source_session_id"],
            created_at=_dt(r["created_at"]), updated_at=_dt(r["updated_at"]),
        )


# --------------------------------------------------------------------------
# Camada 3 — Conhecimento (grafo + busca híbrida)
# --------------------------------------------------------------------------
class KnowledgeStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert_node(self, node: KnowledgeNode) -> KnowledgeNode:
        """Conceito já existente é reaproveitado, não duplicado (Cap. 117:
        'o conhecimento deverá ser reutilizável, nunca duplicado')."""
        if node.node_type == "Conceito":
            existing = self.db.connect().execute(
                """SELECT * FROM knowledge_nodes
                   WHERE user_id = ? AND node_type = 'Conceito' AND lower(title) = lower(?)""",
                (node.user_id, node.title),
            ).fetchone()
            if existing:
                return self._to_node(existing)

        with self.db.tx() as c:
            c.execute(
                """INSERT INTO knowledge_nodes
                   (id, user_id, node_type, title, description, metadata, privacy, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (node.id, node.user_id, node.node_type, node.title, node.description,
                 _json(node.metadata), node.privacy.value,
                 _iso(node.created_at), _iso(node.updated_at)),
            )
        return node

    def add_edge(self, edge: KnowledgeEdge) -> KnowledgeEdge:
        with self.db.tx() as c:
            c.execute(
                """INSERT OR IGNORE INTO knowledge_edges
                   (id, user_id, source_id, target_id, rel_type, properties, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (edge.id, edge.user_id, edge.source_id, edge.target_id, edge.rel_type,
                 _json(edge.properties), _iso(edge.created_at)),
            )
        return edge

    def count_nodes(self, user_id: str) -> int:
        return self.db.connect().execute(
            "SELECT COUNT(*) AS n FROM knowledge_nodes WHERE user_id = ?", (user_id,)
        ).fetchone()["n"]

    def get_node(self, user_id: str, node_id: str) -> KnowledgeNode | None:
        r = self.db.connect().execute(
            "SELECT * FROM knowledge_nodes WHERE id = ? AND user_id = ?", (node_id, user_id)
        ).fetchone()
        return self._to_node(r) if r else None

    def neighbors(self, user_id: str, node_id: str, rel_type: str | None = None) -> list[KnowledgeNode]:
        """Vizinhos nos DOIS sentidos.

        O código antigo só olhava arestas de saída. Como a relação
        "ML --EXPLICA--> IA" tinha IA como alvo, perguntar 'o que se conecta a
        IA?' devolvia lista vazia. O grafo existia, mas era cego pra metade dele.
        """
        sql = """
            SELECT n.* FROM knowledge_edges e
            JOIN knowledge_nodes n ON n.id = e.target_id
            WHERE e.user_id = ? AND e.source_id = ? {rel}
            UNION
            SELECT n.* FROM knowledge_edges e
            JOIN knowledge_nodes n ON n.id = e.source_id
            WHERE e.user_id = ? AND e.target_id = ? {rel}
        """
        rel_clause = "AND e.rel_type = ?" if rel_type else ""
        sql = sql.format(rel=rel_clause)
        params: list[Any] = [user_id, node_id]
        if rel_type:
            params.append(rel_type)
        params += [user_id, node_id]
        if rel_type:
            params.append(rel_type)
        rows = self.db.connect().execute(sql, params).fetchall()
        return [self._to_node(r) for r in rows]

    def delete_node(self, user_id: str, node_id: str) -> bool:
        with self.db.tx() as c:
            cur = c.execute(
                "DELETE FROM knowledge_nodes WHERE id = ? AND user_id = ?", (node_id, user_id)
            )
            c.execute(
                "DELETE FROM embeddings WHERE owner_type = 'knowledge_node' AND owner_id = ?",
                (node_id,),
            )
            return cur.rowcount > 0  # arestas somem por ON DELETE CASCADE

    # --- embeddings ---
    def save_embedding(self, user_id: str, node_id: str, model: str, vec: list[float]) -> None:
        with self.db.tx() as c:
            c.execute(
                """INSERT OR REPLACE INTO embeddings
                   (owner_type, owner_id, user_id, model, dim, vector, created_at)
                   VALUES ('knowledge_node', ?, ?, ?, ?, ?, ?)""",
                (node_id, user_id, model, len(vec), vector.to_blob(vec), _iso(utcnow())),
            )

    # --- busca híbrida ---
    def search(
        self,
        user_id: str,
        query: str,
        query_embedding: list[float] | None = None,
        model: str | None = None,
        top_k: int = 6,
    ) -> list[SearchHit]:
        """Busca híbrida: FTS5 (léxica) + cosseno (semântica), fundidas por RRF.

        Isto é o que faltava por completo no código antigo: os embeddings eram
        calculados e gravados, e nunca lidos por ninguém. Custo de API pago,
        benefício zero.
        """
        lexical = self._search_lexical(user_id, query, top_k * 2)
        semantic = (
            self._search_semantic(user_id, query_embedding, model, top_k * 2)
            if query_embedding and model
            else []
        )
        return self._fuse(lexical, semantic, top_k)

    def _search_lexical(self, user_id: str, query: str, limit: int) -> list[str]:
        terms = [t for t in query.replace('"', " ").split() if len(t) > 2]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        rows = self.db.connect().execute(
            """SELECT n.id FROM knowledge_fts f
               JOIN knowledge_nodes n ON n.rowid = f.rowid
               WHERE knowledge_fts MATCH ? AND n.user_id = ?
               ORDER BY bm25(knowledge_fts) LIMIT ?""",
            (match, user_id, limit),
        ).fetchall()
        return [r["id"] for r in rows]

    def _search_semantic(self, user_id: str, query_vec: list[float], model: str, limit: int) -> list[str]:
        rows = self.db.connect().execute(
            """SELECT owner_id, vector FROM embeddings
               WHERE user_id = ? AND owner_type = 'knowledge_node' AND model = ?""",
            (user_id, model),
        ).fetchall()
        candidates = [(r["owner_id"], r["vector"]) for r in rows]
        return [nid for nid, _ in vector.rank(query_vec, candidates, limit)]

    def _fuse(self, lexical: list[str], semantic: list[str], top_k: int) -> list[SearchHit]:
        """Reciprocal Rank Fusion. Não exige que os scores das duas buscas
        estejam na mesma escala — só a posição no ranking importa."""
        k = 60
        scores: dict[str, float] = {}
        origin: dict[str, set[str]] = {}
        for rank_, nid in enumerate(lexical):
            scores[nid] = scores.get(nid, 0.0) + 1 / (k + rank_ + 1)
            origin.setdefault(nid, set()).add("keyword")
        for rank_, nid in enumerate(semantic):
            scores[nid] = scores.get(nid, 0.0) + 1 / (k + rank_ + 1)
            origin.setdefault(nid, set()).add("vector")

        best = sorted(scores.items(), key=lambda kv: -kv[1])[:top_k]
        hits: list[SearchHit] = []
        for nid, score in best:
            node = self.get_node_unchecked(nid)
            if node:
                src = origin[nid]
                matched = "hybrid" if len(src) > 1 else next(iter(src))
                hits.append(SearchHit(node=node, score=round(score, 5), matched_by=matched))
        return hits

    def get_node_unchecked(self, node_id: str) -> KnowledgeNode | None:
        r = self.db.connect().execute(
            "SELECT * FROM knowledge_nodes WHERE id = ?", (node_id,)
        ).fetchone()
        return self._to_node(r) if r else None

    @staticmethod
    def _to_node(r: sqlite3.Row) -> KnowledgeNode:
        return KnowledgeNode(
            id=r["id"], user_id=r["user_id"], node_type=r["node_type"], title=r["title"],
            description=r["description"], metadata=json.loads(r["metadata"]),
            privacy=Privacy(r["privacy"]),
            created_at=_dt(r["created_at"]), updated_at=_dt(r["updated_at"]),
        )


# --------------------------------------------------------------------------
# Camada 4 — Jornadas
# --------------------------------------------------------------------------
class JourneyStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, journey: Journey) -> Journey:
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO journeys
                   (id, user_id, project_id, domain, title, stated_goal, real_goal,
                    diagnosis, status, privacy, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (journey.id, journey.user_id, journey.project_id, journey.domain, journey.title,
                 journey.stated_goal, journey.real_goal, _json(journey.diagnosis),
                 journey.status, journey.privacy.value,
                 _iso(journey.created_at), _iso(journey.updated_at)),
            )
            for step in journey.steps:
                self._insert_step(c, step)
        return journey

    def add_steps(self, user_id: str, journey_id: str, steps: list[JourneyStep]) -> list[JourneyStep]:
        with self.db.tx() as c:
            start = c.execute(
                "SELECT COALESCE(MAX(order_index), -1) + 1 AS n FROM journey_steps WHERE journey_id = ?",
                (journey_id,),
            ).fetchone()["n"]
            for offset, step in enumerate(steps):
                step.order_index = start + offset
                step.journey_id = journey_id
                step.user_id = user_id
                self._insert_step(c, step)
        return steps

    def set_status(self, user_id: str, journey_id: str, status: str) -> bool:
        with self.db.tx() as c:
            cur = c.execute(
                "UPDATE journeys SET status = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (status, _iso(utcnow()), journey_id, user_id),
            )
            return cur.rowcount > 0

    def set_goals(self, user_id: str, journey_id: str, real_goal: str, diagnosis: dict[str, Any]) -> bool:
        with self.db.tx() as c:
            cur = c.execute(
                """UPDATE journeys SET real_goal = ?, diagnosis = ?, status = 'ativa', updated_at = ?
                   WHERE id = ? AND user_id = ?""",
                (real_goal, _json(diagnosis), _iso(utcnow()), journey_id, user_id),
            )
            return cur.rowcount > 0

    def set_step_status(self, user_id: str, step_id: str, status: str) -> bool:
        with self.db.tx() as c:
            cur = c.execute(
                "UPDATE journey_steps SET status = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (status, _iso(utcnow()), step_id, user_id),
            )
            return cur.rowcount > 0

    def get(self, user_id: str, journey_id: str) -> Journey | None:
        conn = self.db.connect()
        r = conn.execute(
            "SELECT * FROM journeys WHERE id = ? AND user_id = ?", (journey_id, user_id)
        ).fetchone()
        if not r:
            return None
        steps = conn.execute(
            "SELECT * FROM journey_steps WHERE journey_id = ? ORDER BY order_index", (journey_id,)
        ).fetchall()
        return self._to_journey(r, steps)

    def list(self, user_id: str, status: str | None = None) -> list[Journey]:
        sql = "SELECT * FROM journeys WHERE user_id = ?"
        params: list[Any] = [user_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY updated_at DESC"
        rows = self.db.connect().execute(sql, params).fetchall()
        return [self.get(user_id, r["id"]) for r in rows]  # type: ignore[misc]

    def active(self, user_id: str) -> Journey | None:
        rows = self.list(user_id, status="ativa")
        return rows[0] if rows else None

    def delete(self, user_id: str, journey_id: str) -> bool:
        with self.db.tx() as c:
            cur = c.execute("DELETE FROM journeys WHERE id = ? AND user_id = ?", (journey_id, user_id))
            return cur.rowcount > 0

    @staticmethod
    def _insert_step(c: sqlite3.Connection, s: JourneyStep) -> None:
        c.execute(
            """INSERT INTO journey_steps
               (id, journey_id, user_id, order_index, title, description, status,
                due_date, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (s.id, s.journey_id, s.user_id, s.order_index, s.title, s.description,
             s.status, _iso(s.due_date), _iso(s.created_at), _iso(s.updated_at)),
        )

    @staticmethod
    def _to_journey(r: sqlite3.Row, step_rows: list[sqlite3.Row]) -> Journey:
        return Journey(
            id=r["id"], user_id=r["user_id"], project_id=r["project_id"], domain=r["domain"],
            title=r["title"], stated_goal=r["stated_goal"], real_goal=r["real_goal"],
            diagnosis=json.loads(r["diagnosis"]), status=r["status"], privacy=Privacy(r["privacy"]),
            created_at=_dt(r["created_at"]), updated_at=_dt(r["updated_at"]),
            steps=[
                JourneyStep(
                    id=s["id"], journey_id=s["journey_id"], user_id=s["user_id"],
                    order_index=s["order_index"], title=s["title"], description=s["description"],
                    status=s["status"], due_date=_dt(s["due_date"]),
                    created_at=_dt(s["created_at"]), updated_at=_dt(s["updated_at"]),
                )
                for s in step_rows
            ],
        )


# --------------------------------------------------------------------------
# Auditoria — Cap. 127
# --------------------------------------------------------------------------
class AuditStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def log(
        self, user_id: str, action: str, owner_type: str, owner_id: str,
        session_id: str | None = None, reason: str = "",
    ) -> None:
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO memory_access_log
                   (id, user_id, session_id, action, owner_type, owner_id, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (new_id(), user_id, session_id, action, owner_type, owner_id, reason, _iso(utcnow())),
            )

    def recent(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.connect().execute(
            "SELECT * FROM memory_access_log WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
