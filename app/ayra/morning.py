"""
Ayra do dia seguinte — um empurrão, uma ação.

Não é um dashboard. Toda manhã (ou na primeira visita do dia) a Ayra olha
estudos, gabinete, finanças e jornada, escolhe UMA coisa e te puxa pra ela.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.core.db import Database
from app.domain.models import DailyBriefing, new_id, utcnow
from app.llm.base import LLM
from app.memory.service import MemoryService

log = logging.getLogger("atlas.ayra.morning")

POLISH_SYSTEM = """Você é a Ayra escrevendo o empurrão do dia para o usuário.

Recebe a ação já escolhida. Reescreva em tom humano, direto, sem enrolação:
- headline: 1 frase curta (máx. 12 palavras)
- action: o que fazer AGORA (máx. 20 palavras)
- why: por que isso importa hoje (1 frase)
- chat_opener: mensagem que a Ayra manda ao abrir a conversa (2-3 frases, português BR)

Sem emojis. Sem lista. Sem "como assistente de IA".
"""


class MorningPolish(BaseModel):
    headline: str = ""
    action: str = ""
    why: str = ""
    chat_opener: str = ""


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _today() -> str:
    return utcnow().astimezone(timezone.utc).date().isoformat()


class MorningBriefingService:
    def __init__(self, db: Database, memory: MemoryService, llm: LLM) -> None:
        self.db = db
        self.memory = memory
        self.llm = llm

    # ---------------------------------------------------------------- persist
    def get_for_day(self, user_id: str, day: str | None = None) -> DailyBriefing | None:
        day = day or _today()
        r = self.db.connect().execute(
            "SELECT * FROM daily_briefings WHERE user_id = ? AND day = ?",
            (user_id, day),
        ).fetchone()
        return self._to_brief(r) if r else None

    def save(self, brief: DailyBriefing) -> DailyBriefing:
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO daily_briefings
                   (id, user_id, day, domain, view, title, action_text, reason,
                    minutes, status, journey_id, ref_id, chat_opener, signals,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, day) DO UPDATE SET
                     domain=excluded.domain, view=excluded.view, title=excluded.title,
                     action_text=excluded.action_text, reason=excluded.reason,
                     minutes=excluded.minutes, status=excluded.status,
                     journey_id=excluded.journey_id, ref_id=excluded.ref_id,
                     chat_opener=excluded.chat_opener, signals=excluded.signals,
                     updated_at=excluded.updated_at""",
                (
                    brief.id, brief.user_id, brief.day, brief.domain, brief.view,
                    brief.title, brief.action_text, brief.reason, brief.minutes,
                    brief.status, brief.journey_id, brief.ref_id, brief.chat_opener,
                    __import__("json").dumps(brief.signals, ensure_ascii=False),
                    _iso(brief.created_at), _iso(brief.updated_at),
                ),
            )
        return brief

    def mark(self, user_id: str, status: str, day: str | None = None) -> DailyBriefing | None:
        brief = self.get_for_day(user_id, day)
        if not brief:
            return None
        if status not in {"pending", "done", "skipped"}:
            raise ValueError("status inválido")
        brief.status = status  # type: ignore[assignment]
        brief.updated_at = utcnow()
        return self.save(brief)

    # ---------------------------------------------------------------- compute
    def collect_candidates(self, user_id: str) -> list[dict[str, Any]]:
        """Prioridade: atrasado > agenda hoje > revisão > crise financeira > jornada > capítulo."""
        edu = self.memory.education.snapshot(user_id)
        cab = self.memory.cabinet.snapshot(user_id)
        health = self.memory.finance.health(user_id)
        journeys = [j for j in self.memory.journeys.list(user_id) if j.status == "ativa"]
        active_j = journeys[0] if journeys else None
        next_step = None
        if active_j:
            for s in active_j.steps:
                if s.status != "concluida":
                    next_step = s
                    break

        now = utcnow()
        today = now.astimezone(timezone.utc).date()
        cands: list[dict[str, Any]] = []

        overdue = self.memory.cabinet.overdue_demands(user_id)
        if overdue:
            d = overdue[0]
            cands.append({
                "priority": 100,
                "domain": "gabinete",
                "view": "cabinet",
                "title": "Demanda atrasada pedindo movimento",
                "action_text": f"Destravar «{d.title}» — atualize status ou marque um retorno.",
                "reason": "Prazo passou e continua aberta.",
                "minutes": 20,
                "ref_id": d.id,
                "journey_id": None,
            })

        if cab.proximo_compromisso and cab.proximo_compromisso.starts_at:
            starts = cab.proximo_compromisso.starts_at
            if starts.tzinfo is None:
                starts = starts.replace(tzinfo=timezone.utc)
            if starts.astimezone(timezone.utc).date() == today:
                a = cab.proximo_compromisso
                cands.append({
                    "priority": 90,
                    "domain": "gabinete",
                    "view": "cabinet",
                    "title": "Compromisso é hoje",
                    "action_text": f"Preparar «{a.title}» — pauta e retorno esperado.",
                    "reason": "Está na agenda de hoje.",
                    "minutes": 25,
                    "ref_id": a.id,
                    "journey_id": None,
                })

        if edu.revisoes_vencidas:
            cands.append({
                "priority": 80,
                "domain": "educacao",
                "view": "education",
                "title": "Revisão vencida esperando você",
                "action_text": f"Fazer {min(edu.revisoes_vencidas, 5)} cartão(ões) de revisão agora.",
                "reason": "Conhecimento sem revisão some.",
                "minutes": 15,
                "ref_id": None,
                "journey_id": None,
            })

        if health.dividas_total > 0:
            debts = self.memory.finance.list_debts(user_id, status="ativa")
            top_debt = sorted(debts, key=lambda d: -d.interest_rate_month)[0] if debts else None
            if top_debt:
                cands.append({
                    "priority": 75,
                    "domain": "financas",
                    "view": "finance",
                    "title": "Atacar a dívida cara",
                    "action_text": (
                        f"Pagar um extra em «{top_debt.name}» "
                        f"({top_debt.interest_rate_month:.1f}% a.m.)."
                    ),
                    "reason": f"R$ {health.dividas_total:.0f} em dívidas ativas.",
                    "minutes": 15,
                    "ref_id": top_debt.id,
                    "journey_id": None,
                })
        if health.categorias_estouradas:
            cands.append({
                "priority": 72,
                "domain": "financas",
                "view": "finance",
                "title": "Orçamento estourou",
                "action_text": "Cortar uma categoria que passou do teto e registrar o gasto.",
                "reason": f"{health.categorias_estouradas} categoria(s) acima do limite.",
                "minutes": 15,
                "ref_id": None,
                "journey_id": None,
            })
        if health.taxa_poupanca < 0 and health.receita_mes > 0:
            cands.append({
                "priority": 70,
                "domain": "financas",
                "view": "finance",
                "title": "Mês no vermelho",
                "action_text": "Cortar ou adiar uma despesa e registrar o ajuste.",
                "reason": f"Despesa R$ {health.despesa_mes:.0f} > receita R$ {health.receita_mes:.0f}.",
                "minutes": 20,
                "ref_id": None,
                "journey_id": None,
            })
        elif health.reserva_meses is not None and health.reserva_meses < 1.5:
            cands.append({
                "priority": 65,
                "domain": "financas",
                "view": "finance",
                "title": "Reserva ainda frágil",
                "action_text": "Transferir um valor mínimo para a reserva hoje.",
                "reason": f"Só {health.reserva_meses} mês(es) de colchão.",
                "minutes": 10,
                "ref_id": None,
                "journey_id": None,
            })

        if next_step and active_j:
            cands.append({
                "priority": 55,
                "domain": active_j.domain or "geral",
                "view": "journeys",
                "title": "Próximo passo da jornada",
                "action_text": f"Avançar: «{next_step.title}».",
                "reason": f"Jornada «{active_j.title}» parada neste passo.",
                "minutes": 30,
                "ref_id": next_step.id,
                "journey_id": active_j.id,
            })

        if edu.capitulos_pendentes and edu.proximos_capitulos:
            ch = edu.proximos_capitulos[0]
            cands.append({
                "priority": 50,
                "domain": "educacao",
                "view": "education",
                "title": "Continuar o estudo",
                "action_text": f"Estudar o capítulo «{ch.title}» com a Ayra.",
                "reason": "Há capítulo em aberto na trilha.",
                "minutes": 25,
                "ref_id": ch.id,
                "journey_id": None,
            })

        if cab.demandas_urgentes and not overdue:
            urg = [
                d for d in cab.recentes
                if d.priority in {"alta", "urgente"} and d.status in {"aberta", "em_andamento", "aguardando"}
            ]
            if urg:
                d = urg[0]
                cands.append({
                    "priority": 85,
                    "domain": "gabinete",
                    "view": "cabinet",
                    "title": "Demanda urgente na fila",
                    "action_text": f"Tratar «{d.title}» antes que vire atraso.",
                    "reason": f"Prioridade {d.priority}.",
                    "minutes": 25,
                    "ref_id": d.id,
                    "journey_id": None,
                })

        if not cands:
            cands.append({
                "priority": 1,
                "domain": "geral",
                "view": "ayra",
                "title": "Um passo pequeno já conta",
                "action_text": "Contar à Ayra o que importa hoje e ela monta o foco.",
                "reason": "Nada urgente no radar — bom momento para escolher direção.",
                "minutes": 10,
                "ref_id": None,
                "journey_id": None,
            })

        cands.sort(key=lambda x: -x["priority"])
        return cands

    async def build(
        self, user_id: str, *, force: bool = False, day: str | None = None
    ) -> DailyBriefing:
        day = day or _today()
        existing = self.get_for_day(user_id, day)
        if existing and not force and existing.status == "pending":
            return existing
        if existing and not force and existing.status == "done":
            return existing

        exclude_refs: set[str] = set()
        exclude_titles: set[str] = set()
        if existing and (force or existing.status == "skipped"):
            if existing.ref_id:
                exclude_refs.add(existing.ref_id)
            exclude_titles.add(existing.title)

        cands = self.collect_candidates(user_id)
        if exclude_refs or exclude_titles:
            filtered = [
                c for c in cands
                if (c.get("ref_id") not in exclude_refs) and (c["title"] not in exclude_titles)
            ]
            if filtered:
                cands = filtered
        top = cands[0]
        signals = [
            {"domain": c["domain"], "title": c["title"], "priority": c["priority"]}
            for c in cands[:5]
        ]

        polish = await self._polish(top)
        brief = DailyBriefing(
            id=existing.id if existing else new_id(),
            user_id=user_id,
            day=day,
            domain=top["domain"],
            view=top["view"],
            title=polish.headline or top["title"],
            action_text=polish.action or top["action_text"],
            reason=polish.why or top["reason"],
            minutes=int(top["minutes"]),
            status="pending",
            journey_id=top.get("journey_id"),
            ref_id=top.get("ref_id"),
            chat_opener=polish.chat_opener or self._default_opener(top),
            signals=signals,
            created_at=existing.created_at if existing else utcnow(),
            updated_at=utcnow(),
        )
        return self.save(brief)

    async def _polish(self, top: dict[str, Any]) -> MorningPolish:
        brief_txt = (
            f"Domínio: {top['domain']}\n"
            f"Título: {top['title']}\n"
            f"Ação: {top['action_text']}\n"
            f"Motivo: {top['reason']}\n"
            f"Minutos: {top['minutes']}\n"
        )
        try:
            return await self.llm.extract(POLISH_SYSTEM, brief_txt, MorningPolish)
        except Exception as exc:
            log.warning("polish do briefing falhou: %s", exc)
            return MorningPolish(
                headline=top["title"],
                action=top["action_text"],
                why=top["reason"],
                chat_opener=self._default_opener(top),
            )

    @staticmethod
    def _default_opener(top: dict[str, Any]) -> str:
        return (
            f"Bom dia. Hoje o foco é um só: {top['action_text']} "
            f"({top['minutes']} min). Motivo: {top['reason']} "
            "Vamos começar por aí — me diga se já fez ou se quer que eu te guie."
        )

    def start_payload(self, brief: DailyBriefing) -> dict[str, Any]:
        session_id = new_id()
        self.memory.conversation.ensure_session(
            brief.user_id, session_id, journey_id=brief.journey_id
        )
        return {
            "briefing": brief.model_dump(mode="json"),
            "session_id": session_id,
            "journey_id": brief.journey_id,
            "mensagem_sugerida": brief.chat_opener,
        }

    @staticmethod
    def _to_brief(r: sqlite3.Row) -> DailyBriefing:
        import json
        return DailyBriefing(
            id=r["id"], user_id=r["user_id"], day=r["day"],
            domain=r["domain"], view=r["view"], title=r["title"],
            action_text=r["action_text"], reason=r["reason"],
            minutes=int(r["minutes"]), status=r["status"],
            journey_id=r["journey_id"], ref_id=r["ref_id"],
            chat_opener=r["chat_opener"] or "",
            signals=json.loads(r["signals"] or "[]"),
            created_at=_dt(r["created_at"]) or utcnow(),
            updated_at=_dt(r["updated_at"]) or utcnow(),
        )
