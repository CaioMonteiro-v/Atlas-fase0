"""Configuração. Nenhum segredo no código, nenhum default perigoso."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ATLAS_", extra="ignore")

    env: str = "dev"
    database_path: str = "./data/atlas.db"

    # LLM
    llm_provider: str = "gemini"       # gemini | fake

    gemini_api_key: str = ""
    # gemini-2.0-flash foi DESLIGADO em 03/03/2026. Apontar para ele devolve 429.
    # Free tier hoje (cortado ~50-90% em dez/2025) cobre só Flash e Flash-Lite:
    #   gemini-2.5-flash        10 RPM / 250 por dia   <- padrão, melhor qualidade
    #   gemini-2.5-flash-lite   15 RPM / 1000 por dia  <- use se estourar cota
    gemini_model: str = "gemini-2.5-flash"
    gemini_embed_model: str = "gemini-embedding-001"

    # Quantos turnos até consolidar a conversa em memória permanente.
    # Consolidar a CADA turno gasta uma chamada de API por mensagem — no free
    # tier isso é metade da sua cota indo embora para reprocessar a mesma
    # conversa várias vezes. A cada 6 turnos dá o mesmo resultado.
    consolidate_every: int = 6

    # Auth — Fase 0: um token estático por usuário.
    # Quando houver mais de um usuário, troque por JWT. A troca é local:
    # só `core/security.py` muda; nenhuma rota é tocada.
    api_token: str = ""
    user_id: str = "caio"

    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8000"]

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"


PLACEHOLDER = "cole_sua_chave_do_ai_studio_aqui"


@lru_cache
def get_settings() -> Settings:
    s = Settings()

    if s.is_prod and not s.api_token:
        raise RuntimeError("ATLAS_API_TOKEN é obrigatório em produção.")

    if s.llm_provider == "gemini":
        # Falhar no boot, não na primeira mensagem. Um .env copiado sem editar
        # subia o servidor "com sucesso" e só quebrava com 400 do Google quando
        # o usuário já estava esperando uma resposta na tela.
        if not s.gemini_api_key or s.gemini_api_key == PLACEHOLDER:
            raise RuntimeError(
                "ATLAS_GEMINI_API_KEY não foi preenchida no .env.\n"
                "  -> cole a chave do Google AI Studio, OU\n"
                "  -> use ATLAS_LLM_PROVIDER=fake para rodar sem chave e sem rede."
            )
        # Sem checagem de prefixo: o Google emite mais de um formato de chave
        # ("AIza...", "AQ...."). Validar o formato aqui só cria falso negativo —
        # quem diz se a chave presta é o Google, na primeira chamada.
        if len(s.gemini_api_key) < 20:
            raise RuntimeError("ATLAS_GEMINI_API_KEY parece truncada.")

    return s
