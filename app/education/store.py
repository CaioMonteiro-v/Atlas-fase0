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
    StudyChapter,
    StudyMaterial,
    StudyNote,
    StudyQuiz,
    StudyReviewCard,
    StudySession,
    StudyTrack,
    StudyWeeklyPlan,
    TrackProgress,
    QuizAnswer,
    QuizQuestion,
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
    def create_note(self, note: StudyNote, *, make_review: bool = True) -> StudyNote:
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
        if make_review and (note.content or "").strip():
            label = (note.title or note.topic or "o que você anotou").strip()
            self.create_review_card(
                StudyReviewCard(
                    user_id=note.user_id,
                    track_id=note.track_id,
                    note_id=note.id,
                    prompt=f"O que você aprendeu sobre «{label}»?",
                    answer=note.content.strip(),
                    next_review_at=utcnow() + timedelta(days=1),
                )
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

    # -------------------------------------------------------------- chapters
    def add_chapters(self, user_id: str, track_id: str, chapters: list[StudyChapter]) -> list[StudyChapter]:
        with self.db.tx() as c:
            own = c.execute(
                "SELECT id FROM study_tracks WHERE id = ? AND user_id = ?",
                (track_id, user_id),
            ).fetchone()
            if not own:
                raise ValueError("trilha não encontrada")
            start = c.execute(
                "SELECT COALESCE(MAX(order_index), -1) + 1 AS n FROM study_chapters WHERE track_id = ?",
                (track_id,),
            ).fetchone()["n"]
            for i, ch in enumerate(chapters):
                ch.user_id = user_id
                ch.track_id = track_id
                ch.order_index = start + i
                c.execute(
                    """INSERT INTO study_chapters
                       (id, user_id, track_id, order_index, title, summary, objectives,
                        status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ch.id, user_id, track_id, ch.order_index, ch.title, ch.summary,
                        json.dumps(ch.objectives, ensure_ascii=False), ch.status,
                        _iso(ch.created_at), _iso(ch.updated_at),
                    ),
                )
        return chapters

    def list_chapters(self, user_id: str, track_id: str) -> list[StudyChapter]:
        rows = self.db.connect().execute(
            """SELECT * FROM study_chapters WHERE user_id = ? AND track_id = ?
               ORDER BY order_index""",
            (user_id, track_id),
        ).fetchall()
        return [self._to_chapter(r) for r in rows]

    def get_chapter(self, user_id: str, chapter_id: str) -> StudyChapter | None:
        r = self.db.connect().execute(
            "SELECT * FROM study_chapters WHERE id = ? AND user_id = ?",
            (chapter_id, user_id),
        ).fetchone()
        return self._to_chapter(r) if r else None

    def set_chapter_status(self, user_id: str, chapter_id: str, status: str) -> bool:
        with self.db.tx() as c:
            cur = c.execute(
                "UPDATE study_chapters SET status = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (status, _iso(utcnow()), chapter_id, user_id),
            )
            return cur.rowcount > 0

    def count_pending_chapters(self, user_id: str) -> int:
        r = self.db.connect().execute(
            "SELECT COUNT(*) AS n FROM study_chapters WHERE user_id = ? AND status != 'concluido'",
            (user_id,),
        ).fetchone()
        return int(r["n"])

    # ---------------------------------------------------------------- quizzes
    def create_quiz(self, quiz: StudyQuiz) -> StudyQuiz:
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO study_quizzes
                   (id, user_id, track_id, chapter_id, title, questions, answers,
                    score, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    quiz.id, quiz.user_id, quiz.track_id, quiz.chapter_id, quiz.title,
                    json.dumps([q.model_dump() for q in quiz.questions], ensure_ascii=False),
                    json.dumps([a.model_dump() for a in quiz.answers], ensure_ascii=False),
                    quiz.score, quiz.status, _iso(quiz.created_at), _iso(quiz.updated_at),
                ),
            )
        return quiz

    def get_quiz(self, user_id: str, quiz_id: str) -> StudyQuiz | None:
        r = self.db.connect().execute(
            "SELECT * FROM study_quizzes WHERE id = ? AND user_id = ?",
            (quiz_id, user_id),
        ).fetchone()
        return self._to_quiz(r) if r else None

    def list_quizzes(self, user_id: str, track_id: str | None = None) -> list[StudyQuiz]:
        sql = "SELECT * FROM study_quizzes WHERE user_id = ?"
        params: list[Any] = [user_id]
        if track_id:
            sql += " AND track_id = ?"
            params.append(track_id)
        sql += " ORDER BY created_at DESC"
        rows = self.db.connect().execute(sql, params).fetchall()
        return [self._to_quiz(r) for r in rows]

    def save_quiz_result(
        self, user_id: str, quiz_id: str, answers: list[QuizAnswer], score: float
    ) -> StudyQuiz | None:
        with self.db.tx() as c:
            cur = c.execute(
                """UPDATE study_quizzes
                   SET answers = ?, score = ?, status = 'corrigido', updated_at = ?
                   WHERE id = ? AND user_id = ?""",
                (
                    json.dumps([a.model_dump() for a in answers], ensure_ascii=False),
                    score, _iso(utcnow()), quiz_id, user_id,
                ),
            )
            if cur.rowcount == 0:
                return None
        return self.get_quiz(user_id, quiz_id)

    def count_open_quizzes(self, user_id: str) -> int:
        r = self.db.connect().execute(
            "SELECT COUNT(*) AS n FROM study_quizzes WHERE user_id = ? AND status = 'aberto'",
            (user_id,),
        ).fetchone()
        return int(r["n"])

    # -------------------------------------------------------------- materials
    def create_material(self, mat: StudyMaterial) -> StudyMaterial:
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO study_materials
                   (id, user_id, track_id, node_id, title, formato, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    mat.id, mat.user_id, mat.track_id, mat.node_id, mat.title,
                    mat.formato, mat.status, _iso(mat.created_at),
                ),
            )
        return mat

    def update_material(
        self, user_id: str, mat_id: str, *, node_id: str | None = None, status: str | None = None
    ) -> None:
        with self.db.tx() as c:
            if node_id is not None and status is not None:
                c.execute(
                    "UPDATE study_materials SET node_id = ?, status = ? WHERE id = ? AND user_id = ?",
                    (node_id, status, mat_id, user_id),
                )
            elif status is not None:
                c.execute(
                    "UPDATE study_materials SET status = ? WHERE id = ? AND user_id = ?",
                    (status, mat_id, user_id),
                )

    def list_materials(self, user_id: str, track_id: str) -> list[StudyMaterial]:
        rows = self.db.connect().execute(
            """SELECT * FROM study_materials WHERE user_id = ? AND track_id = ?
               ORDER BY created_at DESC""",
            (user_id, track_id),
        ).fetchall()
        return [self._to_material(r) for r in rows]

    # -------------------------------------------------------- revisão espaçada
    def create_review_card(self, card: StudyReviewCard) -> StudyReviewCard:
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO study_review_cards
                   (id, user_id, track_id, note_id, chapter_id, prompt, answer,
                    ease, interval_days, repetitions, next_review_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    card.id, card.user_id, card.track_id, card.note_id, card.chapter_id,
                    card.prompt, card.answer, card.ease, card.interval_days, card.repetitions,
                    _iso(card.next_review_at), _iso(card.created_at), _iso(card.updated_at),
                ),
            )
        return card

    def list_due_reviews(
        self, user_id: str, *, track_id: str | None = None, limit: int = 30
    ) -> list[StudyReviewCard]:
        now = _iso(utcnow())
        sql = "SELECT * FROM study_review_cards WHERE user_id = ? AND next_review_at <= ?"
        params: list[Any] = [user_id, now]
        if track_id:
            sql += " AND track_id = ?"
            params.append(track_id)
        sql += " ORDER BY next_review_at ASC LIMIT ?"
        params.append(limit)
        rows = self.db.connect().execute(sql, params).fetchall()
        return [self._to_review(r) for r in rows]

    def count_due_reviews(self, user_id: str, track_id: str | None = None) -> int:
        now = _iso(utcnow())
        if track_id:
            r = self.db.connect().execute(
                """SELECT COUNT(*) AS n FROM study_review_cards
                   WHERE user_id = ? AND track_id = ? AND next_review_at <= ?""",
                (user_id, track_id, now),
            ).fetchone()
        else:
            r = self.db.connect().execute(
                """SELECT COUNT(*) AS n FROM study_review_cards
                   WHERE user_id = ? AND next_review_at <= ?""",
                (user_id, now),
            ).fetchone()
        return int(r["n"])

    def get_review_card(self, user_id: str, card_id: str) -> StudyReviewCard | None:
        r = self.db.connect().execute(
            "SELECT * FROM study_review_cards WHERE id = ? AND user_id = ?",
            (card_id, user_id),
        ).fetchone()
        return self._to_review(r) if r else None

    def grade_review(self, user_id: str, card_id: str, rating: str) -> StudyReviewCard | None:
        card = self.get_review_card(user_id, card_id)
        if not card:
            return None
        ease = card.ease
        interval = card.interval_days
        reps = card.repetitions

        if rating == "again":
            reps = 0
            interval = 1
            ease = max(1.3, ease - 0.2)
        elif rating == "hard":
            interval = max(1, round(interval * 1.2))
            ease = max(1.3, ease - 0.15)
            reps += 1
        elif rating == "easy":
            if reps == 0:
                interval = 4
            elif reps == 1:
                interval = 10
            else:
                interval = max(1, round(interval * ease * 1.3))
            ease = min(3.0, ease + 0.15)
            reps += 1
        else:  # good
            if reps == 0:
                interval = 1
            elif reps == 1:
                interval = 6
            else:
                interval = max(1, round(interval * ease))
            reps += 1

        now = utcnow()
        next_at = now + timedelta(days=interval)
        with self.db.tx() as c:
            c.execute(
                """UPDATE study_review_cards
                   SET ease = ?, interval_days = ?, repetitions = ?,
                       next_review_at = ?, updated_at = ?
                   WHERE id = ? AND user_id = ?""",
                (ease, interval, reps, _iso(next_at), _iso(now), card_id, user_id),
            )
        return self.get_review_card(user_id, card_id)

    # ---------------------------------------------------------- plano semanal
    @staticmethod
    def monday_of(dt: datetime | None = None) -> str:
        d = (dt or utcnow()).date()
        return (d - timedelta(days=d.weekday())).isoformat()

    def upsert_weekly_plan(self, plan: StudyWeeklyPlan) -> StudyWeeklyPlan:
        existing = self.get_weekly_plan(plan.user_id, plan.week_start, plan.track_id)
        now = utcnow()
        if existing:
            with self.db.tx() as c:
                c.execute(
                    """UPDATE study_weekly_plans
                       SET target_minutes = ?, target_sessions = ?, updated_at = ?
                       WHERE id = ? AND user_id = ?""",
                    (plan.target_minutes, plan.target_sessions, _iso(now), existing.id, plan.user_id),
                )
            existing.target_minutes = plan.target_minutes
            existing.target_sessions = plan.target_sessions
            existing.updated_at = now
            return existing
        plan.created_at = now
        plan.updated_at = now
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO study_weekly_plans
                   (id, user_id, track_id, week_start, target_minutes, target_sessions,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    plan.id, plan.user_id, plan.track_id, plan.week_start,
                    plan.target_minutes, plan.target_sessions,
                    _iso(plan.created_at), _iso(plan.updated_at),
                ),
            )
        return plan

    def get_weekly_plan(
        self, user_id: str, week_start: str, track_id: str | None = None
    ) -> StudyWeeklyPlan | None:
        if track_id:
            r = self.db.connect().execute(
                """SELECT * FROM study_weekly_plans
                   WHERE user_id = ? AND week_start = ? AND track_id = ?""",
                (user_id, week_start, track_id),
            ).fetchone()
        else:
            r = self.db.connect().execute(
                """SELECT * FROM study_weekly_plans
                   WHERE user_id = ? AND week_start = ? AND track_id IS NULL""",
                (user_id, week_start),
            ).fetchone()
        return self._to_weekly(r) if r else None

    def weekly_plan_status(
        self, user_id: str, *, track_id: str | None = None
    ) -> dict[str, Any]:
        week = self.monday_of()
        plan = self.get_weekly_plan(user_id, week, track_id)
        start = datetime.fromisoformat(week)
        # sessions usam utcnow() (aware); week_start é data ingênua
        if start.tzinfo is None and utcnow().tzinfo is not None:
            start = start.replace(tzinfo=utcnow().tzinfo)
        end = start + timedelta(days=7)
        sessions = [
            s for s in self.list_sessions(user_id, track_id=track_id, limit=200)
            if s.occurred_at and start <= s.occurred_at < end
        ]
        minutes = sum(s.minutes for s in sessions)
        n_sess = len(sessions)
        target_m = plan.target_minutes if plan else 180
        target_s = plan.target_sessions if plan else 3
        return {
            "week_start": week,
            "plan": plan.model_dump(mode="json") if plan else None,
            "target_minutes": target_m,
            "target_sessions": target_s,
            "minutes_done": minutes,
            "sessions_done": n_sess,
            "pct_minutes": min(100.0, round(100 * minutes / max(1, target_m), 1)),
            "pct_sessions": min(100.0, round(100 * n_sess / max(1, target_s), 1)),
        }

    # ------------------------------------------------------------- progresso
    def track_progress(self, user_id: str, track_id: str) -> TrackProgress | None:
        track = self.get_track(user_id, track_id)
        if not track:
            return None
        chapters = self.list_chapters(user_id, track_id)
        done = sum(1 for c in chapters if c.status == "concluido")
        total = len(chapters)
        quizzes = self.list_quizzes(user_id, track_id)
        scored = [q.score for q in quizzes if q.score is not None]
        week = self.monday_of()
        start = datetime.fromisoformat(week)
        if start.tzinfo is None and utcnow().tzinfo is not None:
            start = start.replace(tzinfo=utcnow().tzinfo)
        end = start + timedelta(days=7)
        minutes = sum(
            s.minutes for s in self.list_sessions(user_id, track_id=track_id, limit=200)
            if s.occurred_at and start <= s.occurred_at < end
        )
        return TrackProgress(
            track_id=track.id,
            title=track.title,
            subject_area=track.subject_area,
            chapters_total=total,
            chapters_done=done,
            chapters_pct=round(100 * done / total, 1) if total else 0.0,
            notes=len(self.list_notes(user_id, track_id=track_id, limit=500)),
            quizzes=len(quizzes),
            quiz_avg_score=round(sum(scored) / len(scored), 2) if scored else None,
            materials=len(self.list_materials(user_id, track_id)),
            reviews_due=self.count_due_reviews(user_id, track_id),
            minutes_week=minutes,
            chapters=chapters,
        )

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
        proximos: list[StudyChapter] = []
        for t in tracks[:5]:
            for ch in self.list_chapters(user_id, t.id):
                if ch.status != "concluido":
                    proximos.append(ch)
                    break
        return EducationSnapshot(
            tracks_ativas=tracks,
            sessoes_recentes=sessions,
            competencias=comps,
            notas_recentes=notes,
            proximos_capitulos=proximos,
            revisoes_vencidas=self.count_due_reviews(user_id),
            plano_semana=self.weekly_plan_status(user_id),
            capitulos_pendentes=self.count_pending_chapters(user_id),
            quizzes_abertos=self.count_open_quizzes(user_id),
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

    @staticmethod
    def _to_chapter(r: sqlite3.Row) -> StudyChapter:
        return StudyChapter(
            id=r["id"], user_id=r["user_id"], track_id=r["track_id"],
            order_index=r["order_index"], title=r["title"], summary=r["summary"],
            objectives=json.loads(r["objectives"] or "[]"), status=r["status"],
            created_at=_dt(r["created_at"]), updated_at=_dt(r["updated_at"]),
        )

    @staticmethod
    def _to_quiz(r: sqlite3.Row) -> StudyQuiz:
        qs = [QuizQuestion(**q) for q in json.loads(r["questions"] or "[]")]
        ans = [QuizAnswer(**a) for a in json.loads(r["answers"] or "[]")]
        return StudyQuiz(
            id=r["id"], user_id=r["user_id"], track_id=r["track_id"], chapter_id=r["chapter_id"],
            title=r["title"], questions=qs, answers=ans, score=r["score"], status=r["status"],
            created_at=_dt(r["created_at"]), updated_at=_dt(r["updated_at"]),
        )

    @staticmethod
    def _to_material(r: sqlite3.Row) -> StudyMaterial:
        return StudyMaterial(
            id=r["id"], user_id=r["user_id"], track_id=r["track_id"], node_id=r["node_id"],
            title=r["title"], formato=r["formato"], status=r["status"],
            created_at=_dt(r["created_at"]),
        )

    @staticmethod
    def _to_review(r: sqlite3.Row) -> StudyReviewCard:
        return StudyReviewCard(
            id=r["id"], user_id=r["user_id"], track_id=r["track_id"],
            note_id=r["note_id"], chapter_id=r["chapter_id"],
            prompt=r["prompt"], answer=r["answer"] or "",
            ease=float(r["ease"]), interval_days=int(r["interval_days"]),
            repetitions=int(r["repetitions"]),
            next_review_at=_dt(r["next_review_at"]) or utcnow(),
            created_at=_dt(r["created_at"]) or utcnow(),
            updated_at=_dt(r["updated_at"]) or utcnow(),
        )

    @staticmethod
    def _to_weekly(r: sqlite3.Row) -> StudyWeeklyPlan:
        return StudyWeeklyPlan(
            id=r["id"], user_id=r["user_id"], track_id=r["track_id"],
            week_start=r["week_start"],
            target_minutes=int(r["target_minutes"]),
            target_sessions=int(r["target_sessions"]),
            created_at=_dt(r["created_at"]) or utcnow(),
            updated_at=_dt(r["updated_at"]) or utcnow(),
        )
