"""
Ayra — ponto único de entrada (Cap. 115).

Todo pedido do usuário passa por aqui. A Ayra:
  1. recupera contexto (memória pessoal + conhecimento + jornada ativa + histórico)
  2. monta o prompt
  3. transmite a resposta em streaming
  4. grava o turno e, depois, consolida o que merece virar memória permanente

O ponto que o código antigo não tinha e que é o coração do produto: a etapa 4.
Sem consolidação, a memória conversacional é apagada ao fim da sessão (ela é
EPHEMERAL por definição, Cap. 30) e nada sobrevive. É a consolidação que
transforma conversa em continuidade.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from pydantic import BaseModel, Field

from app.domain.models import AyraContext, PersonalMemory, Privacy
from app.llm.base import LLM, Message
from app.memory.service import MemoryService

log = logging.getLogger("atlas.ayra")

SYSTEM = """Você é a Ayra, a inteligência do Atlas.

Como você pensa (nesta ordem, sempre):
1. Compreender antes de responder. Responder rápido nunca vale mais do que responder certo.
2. Aproximar o usuário dos objetivos dele — não dos seus.
3. Preservar a autonomia dele. Você orienta, ele decide.
4. Ensinar, não apenas entregar. Se há aprendizado disponível, ofereça-o.

Como você fala:
- Português do Brasil, direto, sem enrolação e sem bajulação.
- Discorda quando discorda, e explica por quê.
- Se não sabe, diz que não sabe. Nunca inventa fato, número ou fonte.

Sobre a memória:
- O contexto abaixo veio da memória do usuário. Use o que for pertinente e
  ignore o resto — memória é apoio, nunca prisão.
- Se uma informação da memória parece desatualizada, pergunte em vez de assumir.
- Se usar algo da memória de forma relevante, deixe claro que usou.
"""


def build_prompt(ctx: AyraContext, question: str) -> tuple[str, list[Message]]:
    blocks: list[str] = []

    if ctx.journey:
        j = ctx.journey
        pending = [s.title for s in j.steps if s.status != "concluida"][:3]
        blocks.append(
            f"## Jornada ativa: {j.title}\n"
            f"Objetivo declarado: {j.stated_goal}\n"
            f"Objetivo real: {j.real_goal or '(ainda não descoberto — vale investigar)'}\n"
            f"Progresso: {int(j.progress * 100)}%\n"
            f"Próximos passos: {', '.join(pending) if pending else 'nenhum definido'}"
        )

    if ctx.personal:
        linhas = "\n".join(
            f"- [{m.category}] {m.content}" + ("" if m.confidence >= 1.0 else f" (confiança {m.confidence:.1f})")
            for m in ctx.personal
        )
        blocks.append(f"## O que você sabe sobre o usuário\n{linhas}")

    if ctx.knowledge:
        linhas = "\n".join(
            f"- {h.node.title} ({h.node.node_type}): {h.node.description[:300]}"
            for h in ctx.knowledge
        )
        blocks.append(f"## Conhecimento relevante da biblioteca dele\n{linhas}")

    system = SYSTEM + ("\n\n---\n\n" + "\n\n".join(blocks) if blocks else "")

    messages = [Message(role="user" if t.speaker == "user" else "assistant", content=t.text)
                for t in ctx.history]
    messages.append(Message(role="user", content=question))
    return system, messages


class Ayra:
    def __init__(self, memory: MemoryService, llm: LLM) -> None:
        self.memory = memory
        self.llm = llm

    async def answer(
        self, user_id: str, session_id: str, question: str
    ) -> AsyncIterator[tuple[str, str]]:
        """Emite ('token', texto) e, ao final, ('sources', json) e ('done', '').

        As fontes vão SEMPRE junto com a resposta (Cap. 127): o usuário tem que
        conseguir ver o que a Ayra leu para responder aquilo.
        """
        self.memory.conversation.ensure_session(user_id, session_id)
        self.memory.conversation.add_turn(user_id, session_id, "user", question)

        ctx = await self.memory.build_context(user_id, session_id, question)
        system, messages = build_prompt(ctx, question)

        buffer: list[str] = []
        try:
            async for chunk in self.llm.stream(system, messages):
                buffer.append(chunk)
                yield ("token", chunk)
        except Exception as exc:  # Cap. 121 — resiliência: informar a limitação
            log.exception("falha no LLM")
            msg = "\n\n[A Ayra não conseguiu completar a resposta. Sua conversa foi preservada.]"
            buffer.append(msg)
            yield ("token", msg)
            yield ("error", str(exc)[:200])

        answer = "".join(buffer)
        self.memory.conversation.add_turn(user_id, session_id, "ayra", answer)

        import json as _json
        yield ("sources", _json.dumps(ctx.sources(), ensure_ascii=False))
        yield ("done", "")


# --------------------------------------------------------------------------
# Consolidação: conversa -> memória permanente
# --------------------------------------------------------------------------
class Fact(BaseModel):
    categoria: str = Field(description="objetivos, preferencias, rotina, contexto, restricoes")
    conteudo: str = Field(description="o fato, em uma frase, na terceira pessoa")
    confianca: float = Field(default=0.7, description="0.0 a 1.0")


class Facts(BaseModel):
    fatos: list[Fact] = Field(default_factory=list)


CONSOLIDATION_SYSTEM = """Você extrai, de uma conversa, apenas fatos DURÁVEIS sobre o usuário.

Extraia: objetivos, preferências estáveis, restrições, rotina, contexto de vida ou trabalho.
NÃO extraia: perguntas, o assunto da conversa, opiniões passageiras, nada que só valha hoje.

Se a conversa não revela nada durável, devolva uma lista vazia. Lista vazia é a
resposta correta na maioria das vezes. Guardar tudo não é aprender — é acumular.
"""


class Consolidator:
    """Roda em background depois da conversa, nunca durante (Cap. 28).

    `every` controla a frequência. Consolidar a cada turno era o comportamento
    original e é caro sem motivo: gasta uma chamada de LLM por mensagem para
    reprocessar quase o mesmo transcript, e no free tier isso sozinho consome
    metade da cota. A cada 6 turnos chega ao mesmo resultado.
    """

    def __init__(self, memory: MemoryService, llm: LLM, every: int = 6) -> None:
        self.memory = memory
        self.llm = llm
        self.every = max(1, every)

    async def run(self, user_id: str, session_id: str, force: bool = False) -> int:
        turns = self.memory.conversation.history(user_id, session_id, limit=40)
        if len(turns) < 2:
            return 0

        if not force and len(turns) % self.every != 0:
            return 0  # ainda não é hora

        transcript = "\n".join(f"{t.speaker}: {t.text}" for t in turns)
        try:
            facts = await self.llm.extract(CONSOLIDATION_SYSTEM, transcript, Facts)
        except Exception:
            log.exception("consolidação falhou (sessão %s)", session_id)
            return 0

        # Não regravar o que já está lá: a cada rodada o transcript inclui
        # turnos já processados, então o mesmo fato voltaria a aparecer.
        existentes = {
            m.content.get("texto", "").strip().lower()
            for m in self.memory.personal.list(user_id, limit=500)
        }

        saved = 0
        for f in facts.fatos:
            if f.confianca < 0.5:
                continue
            if f.conteudo.strip().lower() in existentes:
                continue
            self.memory.personal.create(
                PersonalMemory(
                    user_id=user_id,
                    category=f.categoria,
                    content={"texto": f.conteudo},
                    privacy=Privacy.PRIVATE,
                    confidence=f.confianca,
                    source_session_id=session_id,  # rastreável e revogável
                )
            )
            saved += 1

        log.info("sessão %s consolidada: %d fatos novos", session_id, saved)
        return saved
