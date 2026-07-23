"""Dependências. Objetos caros (banco, cliente HTTP) são criados uma vez no
lifespan e apenas entregues aqui."""

from __future__ import annotations

from fastapi import Request

from app.ayra.orchestrator import Ayra, Consolidator
from app.core.db import Database
from app.knowledge.ingest import DocumentIngestor
from app.llm.base import LLM
from app.memory.service import MemoryService


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_llm(request: Request) -> LLM:
    return request.app.state.llm


def get_memory(request: Request) -> MemoryService:
    return request.app.state.memory


def get_ayra(request: Request) -> Ayra:
    return Ayra(request.app.state.memory, request.app.state.llm)


def get_consolidator(request: Request) -> Consolidator:
    return Consolidator(
        request.app.state.memory,
        request.app.state.llm,
        every=request.app.state.settings.consolidate_every,
    )


def get_ingestor(request: Request) -> DocumentIngestor:
    return DocumentIngestor(request.app.state.memory, request.app.state.llm)
