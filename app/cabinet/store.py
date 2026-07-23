"""
Persistência do Domínio Gabinete Inteligente (Cap. 97–105).

Cidadão no centro: demandas, linha do tempo e agenda com contexto municipal.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from app.core.db import Database
from app.domain.models import (
    CabinetAgendaItem,
    CabinetCitizen,
    CabinetDemand,
    CabinetSnapshot,
    CabinetTimelineEvent,
    Privacy,
    utcnow,
)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class CabinetStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # --------------------------------------------------------------- citizens
    def create_citizen(self, citizen: CabinetCitizen) -> CabinetCitizen:
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO cabinet_citizens
                   (id, user_id, name, municipality, contact, notes, privacy, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    citizen.id, citizen.user_id, citizen.name, citizen.municipality,
                    citizen.contact, citizen.notes, citizen.privacy.value,
                    _iso(citizen.created_at), _iso(citizen.updated_at),
                ),
            )
        return citizen

    def list_citizens(self, user_id: str, municipality: str | None = None) -> list[CabinetCitizen]:
        sql = "SELECT * FROM cabinet_citizens WHERE user_id = ?"
        params: list[Any] = [user_id]
        if municipality:
            sql += " AND municipality = ?"
            params.append(municipality)
        sql += " ORDER BY name"
        rows = self.db.connect().execute(sql, params).fetchall()
        return [self._to_citizen(r) for r in rows]

    def get_citizen(self, user_id: str, citizen_id: str) -> CabinetCitizen | None:
        r = self.db.connect().execute(
            "SELECT * FROM cabinet_citizens WHERE id = ? AND user_id = ?",
            (citizen_id, user_id),
        ).fetchone()
        return self._to_citizen(r) if r else None

    # ---------------------------------------------------------------- demands
    def create_demand(self, demand: CabinetDemand) -> CabinetDemand:
        with self.db.tx() as c:
            if demand.citizen_id:
                own = c.execute(
                    "SELECT id FROM cabinet_citizens WHERE id = ? AND user_id = ?",
                    (demand.citizen_id, demand.user_id),
                ).fetchone()
                if not own:
                    raise ValueError("cidadão não encontrado")
            c.execute(
                """INSERT INTO cabinet_demands
                   (id, user_id, citizen_id, title, subject, municipality, category,
                    priority, status, origin, assignee, due_date, result, privacy,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    demand.id, demand.user_id, demand.citizen_id, demand.title,
                    demand.subject, demand.municipality, demand.category,
                    demand.priority, demand.status, demand.origin, demand.assignee,
                    _iso(demand.due_date), demand.result, demand.privacy.value,
                    _iso(demand.created_at), _iso(demand.updated_at),
                ),
            )
        return demand

    def list_demands(
        self, user_id: str, *, status: str | None = None, municipality: str | None = None, limit: int = 50
    ) -> list[CabinetDemand]:
        sql = "SELECT * FROM cabinet_demands WHERE user_id = ?"
        params: list[Any] = [user_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        if municipality:
            sql += " AND municipality = ?"
            params.append(municipality)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self.db.connect().execute(sql, params).fetchall()
        return [self._to_demand(r) for r in rows]

    def get_demand(self, user_id: str, demand_id: str) -> CabinetDemand | None:
        r = self.db.connect().execute(
            "SELECT * FROM cabinet_demands WHERE id = ? AND user_id = ?",
            (demand_id, user_id),
        ).fetchone()
        return self._to_demand(r) if r else None

    def update_demand(self, user_id: str, demand_id: str, patch: dict[str, Any]) -> CabinetDemand | None:
        if not patch:
            return self.get_demand(user_id, demand_id)
        allowed = {"status", "priority", "assignee", "result", "subject"}
        fields = {k: v for k, v in patch.items() if k in allowed and v is not None}
        if not fields:
            return self.get_demand(user_id, demand_id)
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [_iso(utcnow()), demand_id, user_id]
        with self.db.tx() as c:
            cur = c.execute(
                f"UPDATE cabinet_demands SET {sets}, updated_at = ? WHERE id = ? AND user_id = ?",
                values,
            )
            if cur.rowcount == 0:
                return None
        return self.get_demand(user_id, demand_id)

    # --------------------------------------------------------------- timeline
    def add_timeline(self, event: CabinetTimelineEvent) -> CabinetTimelineEvent:
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO cabinet_timeline
                   (id, user_id, citizen_id, demand_id, municipality, event_type,
                    title, description, occurred_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.id, event.user_id, event.citizen_id, event.demand_id,
                    event.municipality, event.event_type, event.title, event.description,
                    _iso(event.occurred_at), _iso(event.created_at),
                ),
            )
        return event

    def list_timeline(
        self, user_id: str, *, citizen_id: str | None = None, limit: int = 40
    ) -> list[CabinetTimelineEvent]:
        sql = "SELECT * FROM cabinet_timeline WHERE user_id = ?"
        params: list[Any] = [user_id]
        if citizen_id:
            sql += " AND citizen_id = ?"
            params.append(citizen_id)
        sql += " ORDER BY occurred_at DESC LIMIT ?"
        params.append(limit)
        rows = self.db.connect().execute(sql, params).fetchall()
        return [self._to_timeline(r) for r in rows]

    # ----------------------------------------------------------------- agenda
    def create_agenda(self, item: CabinetAgendaItem) -> CabinetAgendaItem:
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO cabinet_agenda
                   (id, user_id, title, municipality, related_demand_id, starts_at,
                    notes, status, privacy, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.id, item.user_id, item.title, item.municipality,
                    item.related_demand_id, _iso(item.starts_at), item.notes,
                    item.status, item.privacy.value, _iso(item.created_at), _iso(item.updated_at),
                ),
            )
        return item

    def list_agenda(self, user_id: str, limit: int = 20) -> list[CabinetAgendaItem]:
        rows = self.db.connect().execute(
            """SELECT * FROM cabinet_agenda WHERE user_id = ?
               ORDER BY starts_at ASC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        return [self._to_agenda(r) for r in rows]

    # -------------------------------------------------------------- snapshot
    def snapshot(self, user_id: str) -> CabinetSnapshot:
        open_statuses = ("aberta", "em_andamento", "aguardando")
        all_open = []
        for st in open_statuses:
            all_open.extend(self.list_demands(user_id, status=st, limit=100))
        urgentes = [d for d in all_open if d.priority in {"alta", "urgente"}]
        municipios = sorted({d.municipality for d in all_open if d.municipality})
        recentes = sorted(all_open, key=lambda d: d.updated_at or utcnow(), reverse=True)[:8]
        agenda = [a for a in self.list_agenda(user_id, limit=10) if a.status == "agendado"]
        return CabinetSnapshot(
            demandas_abertas=len(all_open),
            demandas_urgentes=len(urgentes),
            municipios=municipios,
            recentes=recentes,
            agenda=agenda,
        )

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _to_citizen(r: sqlite3.Row) -> CabinetCitizen:
        return CabinetCitizen(
            id=r["id"], user_id=r["user_id"], name=r["name"], municipality=r["municipality"],
            contact=r["contact"], notes=r["notes"], privacy=Privacy(r["privacy"]),
            created_at=_dt(r["created_at"]), updated_at=_dt(r["updated_at"]),
        )

    @staticmethod
    def _to_demand(r: sqlite3.Row) -> CabinetDemand:
        return CabinetDemand(
            id=r["id"], user_id=r["user_id"], citizen_id=r["citizen_id"], title=r["title"],
            subject=r["subject"], municipality=r["municipality"], category=r["category"],
            priority=r["priority"], status=r["status"], origin=r["origin"],
            assignee=r["assignee"], due_date=_dt(r["due_date"]), result=r["result"],
            privacy=Privacy(r["privacy"]),
            created_at=_dt(r["created_at"]), updated_at=_dt(r["updated_at"]),
        )

    @staticmethod
    def _to_timeline(r: sqlite3.Row) -> CabinetTimelineEvent:
        return CabinetTimelineEvent(
            id=r["id"], user_id=r["user_id"], citizen_id=r["citizen_id"], demand_id=r["demand_id"],
            municipality=r["municipality"], event_type=r["event_type"], title=r["title"],
            description=r["description"], occurred_at=_dt(r["occurred_at"]),
            created_at=_dt(r["created_at"]),
        )

    @staticmethod
    def _to_agenda(r: sqlite3.Row) -> CabinetAgendaItem:
        return CabinetAgendaItem(
            id=r["id"], user_id=r["user_id"], title=r["title"], municipality=r["municipality"],
            related_demand_id=r["related_demand_id"], starts_at=_dt(r["starts_at"]),
            notes=r["notes"], status=r["status"], privacy=Privacy(r["privacy"]),
            created_at=_dt(r["created_at"]), updated_at=_dt(r["updated_at"]),
        )
