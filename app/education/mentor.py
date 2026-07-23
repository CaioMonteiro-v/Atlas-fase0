"""
Mentora de estudos — gera capítulos, quizzes e corrige com evidência de competência.
"""

from __future__ import annotations

import logging

from app.domain.models import (
    ChapterPlan,
    Competency,
    PlannedQuiz,
    Privacy,
    QuizAnswer,
    QuizGradeResult,
    QuizQuestion,
    StudyChapter,
    StudyQuiz,
    StudyTrack,
    new_id,
)
from app.llm.base import LLM
from app.memory.service import MemoryService

log = logging.getLogger("atlas.education.mentor")

CHAPTERS_SYSTEM = """Você é a Ayra montando um curso vivo para um aluno.

Dado o tema, nível e objetivo, proponha de 5 a 8 capítulos em ordem didática.
Cada capítulo: title curto, summary (2-3 frases do que será ensinado), objectives (2-4 bullets).

Regras:
- Do fundamento ao avançado.
- Capítulos acionáveis, não genéricos.
- Português do Brasil.
- Adeque ao nível (iniciante/intermediario/avancado).
"""

QUIZ_SYSTEM = """Você é a Ayra criando uma checagem de compreensão.

Gere 3 a 5 perguntas sobre o tema/capítulo.
- Prefira perguntas abertas (opcoes vazias) que exijam explicar com as próprias palavras.
- Pode incluir 1 múltipla escolha (3-4 opcoes) se fizer sentido.
- resposta_esperada = o essencial que o aluno deve demonstrar.
- explicacao = feedback curto se errar.

Português do Brasil. Sem pegadinhas.
"""

SIMULADO_SYSTEM = """Você é a Ayra montando um SIMULADO (prova geral) da trilha.

Gere 8 a 12 perguntas cobrindo VÁRIOS capítulos — não foque em um só.
- Misture abertas e no máximo 2 múltipla escolha.
- Cobertura ampla: fundamentos + aplicação + armadilhas.
- resposta_esperada e explicacao obrigatórias.
- title deve começar com "Simulado:".

Português do Brasil. Nível sério de prova, sem pegadinhas injustas.
"""

GRADE_SYSTEM = """Você corrige as respostas de um aluno com justiça e clareza.

Para cada pergunta, diga se está correto (compreensão suficiente, não precisa ser idêntico)
e dê feedback curto. Sugira uma competência e um nível:
iniciar | praticar | proficiente | dominio.

Se a maioria estiver certa → pelo menos praticar. Se excelentes → proficiente.
"""


class StudyMentor:
    def __init__(self, memory: MemoryService, llm: LLM) -> None:
        self.memory = memory
        self.llm = llm

    async def generate_chapters(self, user_id: str, track_id: str) -> list[StudyChapter]:
        track = self.memory.education.get_track(user_id, track_id)
        if not track:
            raise ValueError("trilha não encontrada")

        existing = self.memory.education.list_chapters(user_id, track_id)
        if existing:
            return existing

        brief = (
            f"Tema: {track.title}\n"
            f"Área: {track.subject_area}\n"
            f"Nível: {track.level}\n"
            f"Objetivo: {track.goal or 'compreensão real'}\n"
        )
        plan = await self.llm.extract(CHAPTERS_SYSTEM, brief, ChapterPlan)
        from app.domain.models import PlannedChapter

        if not plan.chapters:
            plan = ChapterPlan(chapters=[
                PlannedChapter(
                    title="Fundamentos",
                    summary=f"Base de {track.title}",
                    objectives=["conceitos essenciais"],
                ),
                PlannedChapter(
                    title="Aplicação",
                    summary="Colocar em prática",
                    objectives=["exercício guiado"],
                ),
                PlannedChapter(
                    title="Aprofundamento",
                    summary="Casos e nuances",
                    objectives=["análise"],
                ),
                PlannedChapter(
                    title="Revisão",
                    summary="Consolidar o aprendizado",
                    objectives=["checagem final"],
                ),
            ])

        chapters = [
            StudyChapter(
                user_id=user_id,
                track_id=track_id,
                title=c.title,
                summary=c.summary,
                objectives=c.objectives,
            )
            for c in plan.chapters
        ]
        return self.memory.education.add_chapters(user_id, track_id, chapters)

    async def generate_quiz(
        self, user_id: str, track_id: str, chapter_id: str | None = None
    ) -> StudyQuiz:
        track = self.memory.education.get_track(user_id, track_id)
        if not track:
            raise ValueError("trilha não encontrada")

        chapter = None
        if chapter_id:
            chapter = self.memory.education.get_chapter(user_id, chapter_id)
            if not chapter or chapter.track_id != track_id:
                raise ValueError("capítulo não encontrado")

        notes = self.memory.education.list_notes(user_id, track_id=track_id, limit=5)
        notes_txt = "\n".join(f"- {n.title}: {n.content[:120]}" for n in notes) or "(sem anotações)"

        brief = (
            f"Tema: {track.title} ({track.subject_area})\n"
            f"Nível: {track.level}\n"
            f"Capítulo: {chapter.title if chapter else 'geral da trilha'}\n"
            f"Resumo do capítulo: {chapter.summary if chapter else '—'}\n"
            f"Anotações do aluno:\n{notes_txt}\n"
        )
        planned = await self.llm.extract(QUIZ_SYSTEM, brief, PlannedQuiz)
        questions = planned.questions or [
            QuizQuestion(
                pergunta=f"Explique, com suas palavras, o conceito central de {track.title}.",
                resposta_esperada="Definição clara do fundamento",
                explicacao="Foque no 'o quê' e no 'por quê'.",
            )
        ]
        # garantir ids
        for q in questions:
            if not q.id:
                q.id = new_id()

        quiz = StudyQuiz(
            user_id=user_id,
            track_id=track_id,
            chapter_id=chapter.id if chapter else None,
            title=planned.title or (f"Checagem: {chapter.title}" if chapter else f"Checagem: {track.title}"),
            questions=questions,
        )
        return self.memory.education.create_quiz(quiz)

    async def generate_simulado(self, user_id: str, track_id: str) -> StudyQuiz:
        track = self.memory.education.get_track(user_id, track_id)
        if not track:
            raise ValueError("trilha não encontrada")

        chapters = self.memory.education.list_chapters(user_id, track_id)
        ch_txt = "\n".join(
            f"- {c.order_index + 1}. {c.title}: {c.summary}" for c in chapters
        ) or "(sem capítulos — cubra o tema geral)"
        notes = self.memory.education.list_notes(user_id, track_id=track_id, limit=8)
        notes_txt = "\n".join(f"- {n.title}: {n.content[:100]}" for n in notes) or "(sem anotações)"

        brief = (
            f"Tema: {track.title} ({track.subject_area})\n"
            f"Nível: {track.level}\n"
            f"Objetivo: {track.goal or 'compreensão real'}\n"
            f"Capítulos:\n{ch_txt}\n"
            f"Anotações do aluno:\n{notes_txt}\n"
        )
        planned = await self.llm.extract(SIMULADO_SYSTEM, brief, PlannedQuiz)
        questions = planned.questions or [
            QuizQuestion(
                pergunta=f"Explique o fio condutor de {track.title} do começo ao fim.",
                resposta_esperada="Visão integrada dos fundamentos",
                explicacao="Mostre conexão entre os capítulos.",
            )
        ]
        for q in questions:
            if not q.id:
                q.id = new_id()

        quiz = StudyQuiz(
            user_id=user_id,
            track_id=track_id,
            chapter_id=None,
            title=planned.title if (planned.title or "").startswith("Simulado")
            else f"Simulado: {track.title}",
            questions=questions,
        )
        return self.memory.education.create_quiz(quiz)

    async def grade_quiz(
        self, user_id: str, quiz_id: str, raw_answers: list[dict]
    ) -> StudyQuiz:
        quiz = self.memory.education.get_quiz(user_id, quiz_id)
        if not quiz:
            raise ValueError("quiz não encontrado")
        if quiz.status == "corrigido":
            return quiz

        track = self.memory.education.get_track(user_id, quiz.track_id)
        qmap = {q.id: q for q in quiz.questions}
        transcript_parts = []
        for a in raw_answers:
            qid = a.get("question_id")
            resp = (a.get("resposta") or "").strip()
            q = qmap.get(qid)
            if not q:
                continue
            transcript_parts.append(
                f"P: {q.pergunta}\nEsperado: {q.resposta_esperada}\nResposta do aluno: {resp}"
            )

        transcript = "\n\n".join(transcript_parts) or "(sem respostas)"
        grade = await self.llm.extract(GRADE_SYSTEM, transcript, QuizGradeResult)

        by_id = {i.question_id: i for i in grade.itens}
        answers: list[QuizAnswer] = []
        correct = 0
        for a in raw_answers:
            qid = a.get("question_id", "")
            resp = (a.get("resposta") or "").strip()
            item = by_id.get(qid)
            ok = bool(item.correto) if item else False
            if ok:
                correct += 1
            answers.append(QuizAnswer(
                question_id=qid,
                resposta=resp,
                correto=ok,
                feedback=(item.feedback if item else "Sem feedback"),
            ))

        total = max(1, len(answers))
        score = round(correct / total, 2)
        saved = self.memory.education.save_quiz_result(user_id, quiz_id, answers, score)
        if not saved:
            raise ValueError("falha ao salvar correção")

        # Evidência de competência
        if track and score >= 0.5:
            nome = grade.competencia_sugerida or track.title
            nivel = grade.nivel_sugerido if score >= 0.75 else "praticar"
            if score >= 0.9:
                nivel = "proficiente"
            status = "adquirida" if score >= 0.85 else "em_desenvolvimento"
            self.memory.education.create_competency(
                Competency(
                    user_id=user_id,
                    track_id=track.id,
                    name=nome[:200],
                    subject_area=track.subject_area,
                    level=nivel,  # type: ignore[arg-type]
                    evidence=f"Quiz «{quiz.title}» — score {int(score * 100)}%",
                    status=status,  # type: ignore[arg-type]
                    privacy=Privacy.PRIVATE,
                )
            )

        # Se quiz de capítulo e score ok, marca capítulo em progresso/concluído
        if quiz.chapter_id and score >= 0.7:
            self.memory.education.set_chapter_status(user_id, quiz.chapter_id, "concluido")
        elif quiz.chapter_id:
            self.memory.education.set_chapter_status(user_id, quiz.chapter_id, "em_progresso")

        return saved
