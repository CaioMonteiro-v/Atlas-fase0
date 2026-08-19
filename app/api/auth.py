"""Login de sessão (cookie) — uso solo no browser sem Bearer em toda request."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.security import SESSION_COOKIE, auth_required, normalize_token, tokens_match

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    token: str = Field(min_length=1)


@router.get("/status")
def auth_status():
    settings = get_settings()
    return {
        "auth_required": auth_required(settings),
        "open_access": settings.open_access,
        "user_id": settings.user_id,
    }


@router.post("/login")
def login(body: LoginBody, response: Response):
    settings = get_settings()
    if not auth_required(settings):
        response.delete_cookie(SESSION_COOKIE)
        return {"ok": True, "mode": "open"}

    expected = normalize_token(settings.api_token)
    provided = normalize_token(body.token)
    if not tokens_match(provided, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token inválido")

    response.set_cookie(
        key=SESSION_COOKIE,
        value=expected,
        httponly=True,
        secure=settings.is_prod,
        samesite="lax",
        max_age=60 * 60 * 24 * 180,  # 180 dias
        path="/",
    )
    return {"ok": True, "mode": "session"}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}
