"""
Autenticação.

A regra: `user_id` é DERIVADO da autenticação. Nenhuma rota aceita `user_id` no corpo.

Modos:
  - sem ATLAS_API_TOKEN (dev): libera
  - ATLAS_OPEN_ACCESS=1: libera (uso solo no Render, sem colar token)
  - com token: Bearer OU cookie de sessão (`atlas_session`)
"""

from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings

bearer = HTTPBearer(auto_error=False)
SESSION_COOKIE = "atlas_session"


def normalize_token(value: str | None) -> str:
    """Tira espaços e aspas que o Render/gente cola sem querer."""
    if not value:
        return ""
    return str(value).strip().strip('"').strip("'")


def tokens_match(provided: str | None, expected: str | None) -> bool:
    a = normalize_token(provided)
    b = normalize_token(expected)
    if not a or not b:
        return False
    if len(a) != len(b):
        return False
    return hmac.compare_digest(a, b)


def auth_required(settings: Settings) -> bool:
    if settings.open_access:
        return False
    return bool(normalize_token(settings.api_token))


async def current_user_id(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_settings),
) -> str:
    if not auth_required(settings):
        return settings.user_id

    expected = normalize_token(settings.api_token)
    bearer_ok = creds is not None and tokens_match(creds.credentials, expected)
    cookie_ok = tokens_match(request.cookies.get(SESSION_COOKIE), expected)

    if not bearer_ok and not cookie_ok:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "token inválido",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return settings.user_id
