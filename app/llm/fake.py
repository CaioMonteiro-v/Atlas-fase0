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
        for word in f"[fake] recebi: {last}".split():
            await asyncio.sleep(0.02)
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
        return schema()  # todos os campos de Extraction têm default_factory=list
