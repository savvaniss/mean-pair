"""Authentication helpers and routes."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from datetime import datetime
from typing import Optional

import config
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from database import User, get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])

_session_tokens: dict[str, str] = {}
_session_lock = threading.Lock()


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_]+$")
    password: str = Field(..., min_length=8, max_length=128)
    confirm_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v: str, info):
        password = info.data.get("password")
        if password is not None and v != password:
            raise ValueError("Passwords do not match")
        return v


class UserResponse(BaseModel):
    username: str
    created_at: datetime


SESSION_COOKIE_NAME = "session_token"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"{salt}${digest.hex()}"


def _verify_password(password: str, hashed: str) -> bool:
    try:
        salt, stored_hash = hashed.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
    return hmac.compare_digest(digest, stored_hash)


def _create_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    with _session_lock:
        _session_tokens[token] = username
    return token


def _delete_session(token: str) -> None:
    with _session_lock:
        _session_tokens.pop(token, None)


def _get_username_for_token(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    with _session_lock:
        return _session_tokens.get(token)


@router.get("/config")
async def auth_config():
    return {"registration_enabled": bool(config.AUTH_ALLOW_REGISTRATION)}


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    if not config.AUTH_ALLOW_REGISTRATION:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Registration disabled")

    existing = db.query(User).filter(User.username == payload.username).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken")

    user = User(
        username=payload.username,
        hashed_password=_hash_password(payload.password),
        created_at=datetime.utcnow(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"username": user.username}


@router.post("/login")
async def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not _verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = _create_session(user.username)
    response = JSONResponse({"message": "Logged in", "username": user.username})
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=SESSION_MAX_AGE,
        samesite="lax",
    )
    return response


@router.post("/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        _delete_session(token)
    response = JSONResponse({"message": "Logged out"})
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


def get_current_user_optional(
    request: Request, db: Session = Depends(get_db)
) -> Optional[User]:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    username = _get_username_for_token(token)
    if not username:
        return None
    return db.query(User).filter(User.username == username).first()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_current_user_optional(request, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return UserResponse(username=user.username, created_at=user.created_at)


# Utilities for tests

def reset_sessions() -> None:
    with _session_lock:
        _session_tokens.clear()
