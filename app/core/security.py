"""
Autenticação.

A regra que o código antigo violava em toda função: `user_id` era um PARÂMETRO.
Numa API web isso significa que qualquer um manda `{"user_id": "outro"}` e lê a
memória alheia. IDOR clássico.

Aqui `user_id` é DERIVADO do token. Nenhuma rota aceita `user_id` no corpo.
"""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings

bearer = HTTPBearer(auto_error=False)


async def current_user_id(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_settings),
) -> str:
    if not settings.api_token:  # dev sem token configurado
        if settings.is_prod:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "auth não configurada")
        return settings.user_id

    if creds is None or not secrets.compare_digest(creds.credentials, settings.api_token):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "token inválido",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return settings.user_id
