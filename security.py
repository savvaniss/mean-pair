"""Authentication helpers and dependencies."""
import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta
from typing import Iterable, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

import config  # ensures .env is loaded
from database import SessionLocal, User

SECRET_KEY = os.getenv("AUTH_SECRET_KEY", "change-me").encode()
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
oauth2_optional_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        salt_b64, digest_b64 = hashed_password.split(":", 1)
    except ValueError:
        return False
    salt = base64.urlsafe_b64decode(salt_b64.encode())
    expected = base64.urlsafe_b64decode(digest_b64.encode())
    actual = hashlib.pbkdf2_hmac("sha256", plain_password.encode(), salt, 100_000)
    return hmac.compare_digest(expected, actual)


def get_password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return f"{base64.urlsafe_b64encode(salt).decode()}:{base64.urlsafe_b64encode(digest).decode()}"


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    payload = data.copy()
    expire_at = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    payload["exp"] = int(expire_at.timestamp())
    raw = json.dumps(payload, separators=",:", sort_keys=True)
    signature = hmac.new(SECRET_KEY, raw.encode(), hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{token}.{sig}"


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    return db.query(User).filter(User.username == username).first()


def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
    user = get_user_by_username(db, username)
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user


def get_current_user(db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    user_payload = _decode_token(token)
    username: str | None = user_payload.get("sub")
    exp: int | None = user_payload.get("exp")
    if not username or not exp:
        raise credentials_exception
    if datetime.utcnow().timestamp() > exp:
        raise credentials_exception
    user = get_user_by_username(db, username=username)
    if user is None:
        raise credentials_exception
    return user


def get_optional_user(
    db: Session = Depends(get_db), token: Optional[str] = Depends(oauth2_optional_scheme)
) -> Optional[User]:
    if not token:
        return None
    payload = _decode_token(token)
    username: str | None = payload.get("sub")
    exp: int | None = payload.get("exp")
    if not username or not exp:
        return None
    if datetime.utcnow().timestamp() > exp:
        return None
    return get_user_by_username(db, username=username)


def require_role(roles: Iterable[str]):
    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return current_user

    return dependency


def _decode_token(token: str) -> dict:
    try:
        raw_b64, sig_b64 = token.split(".", 1)
    except ValueError:
        return {}
    pad_raw = "=" * (-len(raw_b64) % 4)
    pad_sig = "=" * (-len(sig_b64) % 4)
    raw = base64.urlsafe_b64decode(raw_b64 + pad_raw)
    provided_sig = base64.urlsafe_b64decode(sig_b64 + pad_sig)
    expected_sig = hmac.new(SECRET_KEY, raw, hashlib.sha256).digest()
    if not hmac.compare_digest(provided_sig, expected_sig):
        return {}
    try:
        return json.loads(raw.decode())
    except json.JSONDecodeError:
        return {}
