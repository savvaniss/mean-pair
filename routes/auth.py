from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from database import User
from security import (
    authenticate_user,
    create_access_token,
    get_current_user,
    get_db,
    get_optional_user,
    get_password_hash,
    require_role,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class Token(BaseModel):
    access_token: str
    token_type: str


class UserOut(BaseModel):
    id: int
    username: str
    role: str

    model_config = ConfigDict(from_attributes=True)


class RegisterRequest(BaseModel):
    username: str
    password: str
    role: Optional[str] = "user"


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    db: Session = Depends(get_db),
    requesting_user: Optional[User] = Depends(get_optional_user),
):
    existing = db.query(User).filter(User.username == payload.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already taken")

    user_count = db.query(User).count()
    desired_role = payload.role or "user"
    if user_count > 0 and desired_role == "admin":
        if not requesting_user or requesting_user.role != "admin":
            raise HTTPException(status_code=403, detail="Admin role requires admin token")

    new_user = User(
        username=payload.username,
        hashed_password=get_password_hash(payload.password),
        role=desired_role if user_count == 0 or (requesting_user and requesting_user.role == "admin") else "user",
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.post("/login", response_model=Token)
def login_for_access_token(payload: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(db, payload.username, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=60)
    access_token = create_access_token(
        data={"sub": user.username, "role": user.role}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserOut)
def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/admin/users", response_model=list[UserOut], dependencies=[Depends(require_role({"admin"}))])
def list_users(db: Session = Depends(get_db)):
    return db.query(User).all()
