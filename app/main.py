"""
Atlas / Ayra — API web.

    uvicorn app.main:app --reload

Camadas (Cap. 114), de cima para baixo:
    api/        Experiência   — HTTP, SSE, validação
    ayra/       Inteligência  — contexto, prompt, consolidação
    knowledge/  Domínios      — ingestão, grafo
    memory/     Memória       — as 4 camadas
    core/       Infraestrutura— banco, config, auth

A regra: cada camada só conhece a de baixo. `memory/` não importa nada de
`api/`. É isso que permite trocar a web por um CLI, um bot do WhatsApp ou um
app mobile sem tocar no núcleo.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import cabinet, chat, education, finance, journeys, memory as memory_routes, morning
from app.api import auth as auth_routes
from app.core.config import get_settings
from app.core.db import Database
from app.core.security import auth_required
from app.llm.fake import FakeLLM
from app.llm.gemini import GeminiLLM
from app.memory.service import MemoryService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("atlas")


def build_llm(settings):
    if settings.llm_provider == "fake":
        log.warning("LLM = FAKE. Nenhuma chamada real ao modelo será feita.")
        return FakeLLM()
    return GeminiLLM(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        embed_model=settings.gemini_embed_model,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    db = Database(settings.database_path)
    db.migrate()
    llm = build_llm(settings)

    app.state.settings = settings
    app.state.db = db
    app.state.llm = llm
    app.state.memory = MemoryService(db, llm)

    log.info("Atlas no ar — banco=%s llm=%s", settings.database_path, llm.name)
    yield

    if hasattr(llm, "aclose"):
        await llm.aclose()
    db.close()


app = FastAPI(
    title="Atlas / Ayra",
    version="0.3.0",
    description=(
        "Plataforma de inteligência: memória, jornadas, educação (estudo geral), "
        "finanças e gabinete — com a Ayra no centro."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,  # nunca "*": as rotas são autenticadas
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_routes.router)
app.include_router(chat.router)
app.include_router(memory_routes.router)
app.include_router(journeys.router)
app.include_router(finance.router)
app.include_router(education.router)
app.include_router(cabinet.router)
app.include_router(morning.router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    """Cap. 121 — resiliência. O usuário recebe uma mensagem útil; o stack trace
    fica no log, não na resposta HTTP."""
    log.exception("erro não tratado em %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"erro": "algo falhou do nosso lado", "caminho": request.url.path},
    )


@app.get("/health", tags=["infra"])
async def health(request: Request):
    settings = get_settings()
    return {
        "status": "ok",
        "llm": request.app.state.llm.name,
        "auth_required": auth_required(settings),
        "open_access": settings.open_access,
    }


# ---------------------------------------------------------------------------
# Console web em "/".
#
# O mount TEM que ser a última coisa do arquivo: StaticFiles casa com qualquer
# caminho, então se viesse antes dos routers engoliria /chat, /docs e o resto.
# Servir o front pelo mesmo servidor também elimina o CORS: mesma origem.
# ---------------------------------------------------------------------------
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    log.info("Console web servido em /")
