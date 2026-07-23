"""
Contrato do modelo de linguagem (Cap. 119 — independência tecnológica).

Três diferenças em relação à ILLMAdapter antiga, todas obrigatórias para web:

1. `async`. Um handler síncrono chamando a API do Gemini trava o event loop
   inteiro do servidor por 2–5 segundos. Com dois usuários simultâneos, já é
   um problema.
2. `stream()`. Sem streaming, o usuário olha para uma tela parada até a
   resposta inteira chegar. Streaming é requisito de produto, não enfeite.
3. `extract()` tipado. O modelo devolve um objeto Pydantic validado, não um
   dict frouxo que estoura três camadas abaixo.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class Message(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class LLM(ABC):
    """Qualquer provedor implementa isto. A Ayra nunca sabe qual está em uso."""

    name: str

    @abstractmethod
    async def stream(self, system: str, messages: list[Message]) -> AsyncIterator[str]:
        """Emite pedaços de texto conforme chegam."""
        raise NotImplementedError
        yield ""  # pragma: no cover

    @abstractmethod
    async def complete(self, system: str, messages: list[Message]) -> str:
        ...

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Lote. Uma chamada por texto é o caminho mais curto para estourar
        o rate limit do free tier."""
        ...

    @property
    @abstractmethod
    def embedding_model(self) -> str:
        """Identifica o modelo do vetor. Vetores de modelos diferentes não são
        comparáveis — por isso o nome é gravado junto com cada embedding."""
        ...

    @abstractmethod
    async def extract(self, system: str, text: str, schema: type[T]) -> T:
        """Saída estruturada validada contra um modelo Pydantic."""
        ...
