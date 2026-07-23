"""
Persistência do Domínio Educação (Cap. 69–81).

Estudo é geral: matemática, direito, medicina, programação, concursos, idiomas…
Idiomas são uma subject_area, nunca o domínio inteiro.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from app.core.db import Database
from app.domain.models import (
    Competency,
    EducationSnapshot,
    Privacy,
    StudyNote,
    StudySession,
    StudyTrack,
    utcnow,
)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class EducationStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ---------------------------------------------------------------- tracks
    def create_track(self, track: StudyTrack) -> StudyTrack:
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO study_tracks
                   (id, user_id, journey_id, title, subject_area, level, goal,
                    status, privacy, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    track.id, track.user_id, track.journey_id, track.title,
                    track.subject_area, track.level, track.goal, track.status,
                    track.privacy.value, _iso(track.created_at), _iso(track.updated_at),
                ),
            )
        return track

    def list_tracks(self, user_id: str, status: str | None = None) -> list[StudyTrack]:
        sql = "SELECT * FROM study_tracks WHERE user_id = ?"
        params: list[Any] = [user_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY updated_at DESC"
        rows = self.db.connect().execute(sql, params).fetchall()
        return [self._to_track(r) for r in rows]

    def get_track(self, user_id: str, track_id: str) -> StudyTrack | None:
        r = self.db.connect().execute(
            "SELECT * FROM study_tracks WHERE id = ? AND user_id = ?",
            (track_id, user_id),
        ).fetchone()
        return self._to_track(r) if r else None

    def delete_track(self, user_id: str, track_id: str) -> bool:
        with self.db.tx() as c:
            cur = c.execute(
                "DELETE FROM study_tracks WHERE id = ? AND user_id = ?",
                (track_id, user_id),
            )
            return cur.rowcount > 0

    # -------------------------------------------------------------- sessions
    def add_session(self, session: StudySession) -> StudySession:
        with self.db.tx() as c:
            own = c.execute(
                "SELECT id FROM study_tracks WHERE id = ? AND user_id = ?",
                (session.track_id, session.user_id),
            ).fetchone()
            if not own:
                raise ValueError("trilha não encontrada")
            c.execute(
                """INSERT INTO study_sessions
                   (id, user_id, track_id, minutes, notes, topics, occurred_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session.id, session.user_id, session.track_id, session.minutes,
                    session.notes, json.dumps(session.topics, ensure_ascii=False),
                    _iso(session.occurred_at), _iso(session.created_at),
                ),
            )
            c.execute(
                "UPDATE study_tracks SET updated_at = ? WHERE id = ? AND user_id = ?",
                (_iso(utcnow()), session.track_id, session.user_id),
            )
        return session

    def list_sessions(
        self, user_id: str, *, track_id: str | None = None, limit: int = 30
    ) -> list[StudySession]:
        sql = "SELECT * FROM study_sessions WHERE user_id = ?"
        params: list[Any] = [user_id]
        if track_id:
            sql += " AND track_id = ?"
            params.append(track_id)
        sql += " ORDER BY occurred_at DESC LIMIT ?"
        params.append(limit)
        rows = self.db.connect().execute(sql, params).fetchall()
        return [self._to_session(r) for r in rows]

    # ---------------------------------------------------------- competencies
    def create_competency(self, comp: Competency) -> Competency:
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO competencies
                   (id, user_id, track_id, name, subject_area, level, evidence,
                    status, privacy, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    comp.id, comp.user_id, comp.track_id, comp.name, comp.subject_area,
                    comp.level, comp.evidence, comp.status, comp.privacy.value,
                    _iso(comp.created_at), _iso(comp.updated_at),
                ),
            )
        return comp

    def list_competencies(self, user_id: str, status: str | None = None) -> list[Competency]:
        sql = "SELECT * FROM competencies WHERE user_id = ?"
        params: list[Any] = [user_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY updated_at DESC"
        rows = self.db.connect().execute(sql, params).fetchall()
        return [self._to_comp(r) for r in rows]

    def update_competency(
        self, user_id: str, comp_id: str, *, level: str | None = None, status: str | None = None,
        evidence: str | None = None,
    ) -> Competency | None:
        current = next((c for c in self.list_competencies(user_id) if c.id == comp_id), None)
        if not current:
            return None
        new_level = level or current.level
        new_status = status or current.status
        new_evidence = evidence if evidence is not None else current.evidence
        with self.db.tx() as c:
            c.execute(
                """UPDATE competencies
                   SET level = ?, status = ?, evidence = ?, updated_at = ?
                   WHERE id = ? AND user_id = ?""",
                (new_level, new_status, new_evidence, _iso(utcnow()), comp_id, user_id),
            )
        return next((c for c in self.list_competencies(user_id) if c.id == comp_id), None)

    # ----------------------------------------------------------------- notes
    def create_note(self, note: StudyNote) -> StudyNote:
        with self.db.tx() as c:
            own = c.execute(
                "SELECT id FROM study_tracks WHERE id = ? AND user_id = ?",
                (note.track_id, note.user_id),
            ).fetchone()
            if not own:
                raise ValueError("trilha não encontrada")
            c.execute(
                """INSERT INTO study_notes
                   (id, user_id, track_id, session_id, title, content, topic, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    note.id, note.user_id, note.track_id, note.session_id,
                    note.title, note.content, note.topic,
                    _iso(note.created_at), _iso(note.updated_at),
                ),
            )
            c.execute(
                "UPDATE study_tracks SET updated_at = ? WHERE id = ? AND user_id = ?",
                (_iso(utcnow()), note.track_id, note.user_id),
            )
        return note

    def list_notes(
        self, user_id: str, *, track_id: str | None = None, limit: int = 50
    ) -> list[StudyNote]:
        sql = "SELECT * FROM study_notes WHERE user_id = ?"
        params: list[Any] = [user_id]
        if track_id:
            sql += " AND track_id = ?"
            params.append(track_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.db.connect().execute(sql, params).fetchall()
        return [self._to_note(r) for r in rows]

    def delete_note(self, user_id: str, note_id: str) -> bool:
        with self.db.tx() as c:
            cur = c.execute(
                "DELETE FROM study_notes WHERE id = ? AND user_id = ?",
                (note_id, user_id),
            )
            return cur.rowcount > 0

    def link_journey(self, user_id: str, track_id: str, journey_id: str) -> bool:
        with self.db.tx() as c:
            cur = c.execute(
                "UPDATE study_tracks SET journey_id = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (journey_id, _iso(utcnow()), track_id, user_id),
            )
            return cur.rowcount > 0

    # -------------------------------------------------------------- snapshot
    def snapshot(self, user_id: str) -> EducationSnapshot:
        tracks = self.list_tracks(user_id, status="ativa")
        sessions = self.list_sessions(user_id, limit=8)
        comps = self.list_competencies(user_id)[:10]
        notes = self.list_notes(user_id, limit=8)
        week_ago = utcnow() - timedelta(days=7)
        week_sessions = [
            s for s in self.list_sessions(user_id, limit=200)
            if s.occurred_at and s.occurred_at >= week_ago
        ]
        areas = sorted({t.subject_area for t in tracks})
        return EducationSnapshot(
            tracks_ativas=tracks,
            sessoes_recentes=sessions,
            competencias=comps,
            notas_recentes=notes,
            minutos_semana=sum(s.minutes for s in week_sessions),
            areas=areas,
        )

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _to_track(r: sqlite3.Row) -> StudyTrack:
        return StudyTrack(
            id=r["id"], user_id=r["user_id"], journey_id=r["journey_id"], title=r["title"],
            subject_area=r["subject_area"], level=r["level"], goal=r["goal"], status=r["status"],
            privacy=Privacy(r["privacy"]),
            created_at=_dt(r["created_at"]), updated_at=_dt(r["updated_at"]),
        )

    @staticmethod
    def _to_session(r: sqlite3.Row) -> StudySession:
        return StudySession(
            id=r["id"], user_id=r["user_id"], track_id=r["track_id"], minutes=r["minutes"],
            notes=r["notes"], topics=json.loads(r["topics"] or "[]"),
            occurred_at=_dt(r["occurred_at"]), created_at=_dt(r["created_at"]),
        )

    @staticmethod
    def _to_comp(r: sqlite3.Row) -> Competency:
        return Competency(
            id=r["id"], user_id=r["user_id"], track_id=r["track_id"], name=r["name"],
            subject_area=r["subject_area"], level=r["level"], evidence=r["evidence"],
            status=r["status"], privacy=Privacy(r["privacy"]),
            created_at=_dt(r["created_at"]), updated_at=_dt(r["updated_at"]),
        )

    @staticmethod
    def _to_note(r: sqlite3.Row) -> StudyNote:
        return StudyNote(
            id=r["id"], user_id=r["user_id"], track_id=r["track_id"], session_id=r["session_id"],
            title=r["title"], content=r["content"], topic=r["topic"],
            created_at=_dt(r["created_at"]), updated_at=_dt(r["updated_at"]),
        )
