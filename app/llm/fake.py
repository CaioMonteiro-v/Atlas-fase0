"""
Adaptador determinístico para desenvolvimento e testes.

Diferença crítica em relação ao "OpenAIAdapter mockado" do código antigo: aquele
mock estava DENTRO do adaptador de produção, com o cliente real comentado. Isso
significa que o dia em que a chave fosse plugada, um caminho de código nunca
executado entraria em produção de uma vez. Aqui o fake é uma classe separada,
escolhida por configuração. O código de produção nunca contém mock.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from typing import TypeVar

from pydantic import BaseModel

from app.llm.base import LLM, Message

T = TypeVar("T", bound=BaseModel)
DIM = 256


class FakeLLM(LLM):
    name = "fake"

    @property
    def embedding_model(self) -> str:
        return "fake-embed-256"

    async def stream(self, system: str, messages: list[Message]) -> AsyncIterator[str]:
        last = messages[-1].content if messages else ""
        lower = last.lower()

        if any(w in lower for w in ("financ", "orçamento", "orcamento", "despesa", "poupar", "investir")):
            reply = (
                "Olhei o seu contexto financeiro. "
                "Antes de qualquer número, me diga o objetivo: reserva, quitar dívida, "
                "investir ou organizar o mês? Com isso monto um plano em passos. "
                f"(recebi: {last[:120]})"
            )
        elif any(w in lower for w in ("jornada", "aprender", "estudar", "plano")):
            reply = (
                "Entendi o pedido. Qual o objetivo real por trás disso — emprego, projeto "
                "pessoal ou curiosidade? Com a resposta eu monto a jornada. "
                f"(recebi: {last[:120]})"
            )
        else:
            reply = (
                f"Estou aqui. Li o que você disse e posso conectar com memória, "
                f"jornadas e finanças quando fizer sentido. {last[:160]}"
            )

        for word in reply.split():
            await asyncio.sleep(0.015)
            yield word + " "

    async def complete(self, system: str, messages: list[Message]) -> str:
        return f"[fake] {messages[-1].content if messages else ''}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Hash determinístico: o mesmo texto sempre gera o mesmo vetor, e textos
        parecidos NÃO geram vetores parecidos. Serve para testar o encanamento,
        não a qualidade da busca."""
        out = []
        for t in texts:
            h = hashlib.sha256(t.encode()).digest()
            out.append([(h[i % len(h)] - 128) / 128 for i in range(DIM)])
        return out

    async def extract(self, system: str, text: str, schema: type[T]) -> T:
        name = schema.__name__

        if name == "JourneyPlan":
            goal = "evoluir no objetivo declarado"
            for line in text.splitlines():
                if line.lower().startswith("objetivo declarado:"):
                    goal = line.split(":", 1)[1].strip() or goal
                    break
            return schema(
                real_goal=goal,
                diagnosis={"nivel": "iniciante", "prazo": "90 dias", "prioridades": "consistência"},
                steps=[
                    {"title": "Mapear o ponto de partida", "description": "O que você já sabe e o que falta."},
                    {"title": "Definir a meta mensurável", "description": "Como saber que avançou."},
                    {"title": "Montar a rotina mínima", "description": "Blocos curtos e repetíveis."},
                    {"title": "Praticar com um projeto real", "description": "Aplicar o aprendizado em algo seu."},
                    {"title": "Revisar e ajustar", "description": "Medir, cortar o que não serve, seguir."},
                ],
            )

        if name == "ChapterPlan":
            tema = "o tema"
            for line in text.splitlines():
                if line.lower().startswith("tema:"):
                    tema = line.split(":", 1)[1].strip() or tema
            return schema(chapters=[
                {"title": f"Fundamentos de {tema}", "summary": "Conceitos essenciais.", "objectives": ["definir", "reconhecer"]},
                {"title": "Estrutura e princípios", "summary": "Como se organiza.", "objectives": ["mapear", "relacionar"]},
                {"title": "Aplicação prática", "summary": "Casos e exemplos.", "objectives": ["aplicar"]},
                {"title": "Erros comuns", "summary": "Armadilhas e como evitar.", "objectives": ["diagnosticar"]},
                {"title": "Aprofundamento", "summary": "Nuances e debate.", "objectives": ["analisar"]},
                {"title": "Revisão e checagem", "summary": "Consolidar.", "objectives": ["explicar com próprias palavras"]},
            ])

        if name == "PlannedQuiz":
            return schema(
                title="Checagem de compreensão",
                questions=[
                    {
                        "id": "q1",
                        "pergunta": "Explique o conceito central com suas palavras.",
                        "opcoes": [],
                        "resposta_esperada": "Definição clara do fundamento",
                        "explicacao": "Foque no o quê e no porquê.",
                    },
                    {
                        "id": "q2",
                        "pergunta": "Dê um exemplo prático desse conceito.",
                        "opcoes": [],
                        "resposta_esperada": "Exemplo concreto e pertinente",
                        "explicacao": "Exemplo genérico demais não basta.",
                    },
                    {
                        "id": "q3",
                        "pergunta": "Qual o erro mais comum de quem está começando?",
                        "opcoes": [],
                        "resposta_esperada": "Identificar uma armadilha real",
                        "explicacao": "Mostre consciência do limite.",
                    },
                ],
            )

        if name == "QuizGradeResult":
            return schema(
                itens=[
                    {"question_id": "q1", "correto": True, "feedback": "Boa explicação."},
                    {"question_id": "q2", "correto": True, "feedback": "Exemplo ok."},
                    {"question_id": "q3", "correto": False, "feedback": "Faltou o erro típico."},
                ],
                competencia_sugerida="Compreensão fundamental",
                nivel_sugerido="praticar",
            )

        return schema()

