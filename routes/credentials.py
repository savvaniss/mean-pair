from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from database import ApiCredential
from security import get_current_user, get_db

router = APIRouter(prefix="/credentials", tags=["credentials"])

ALLOWED_ENGINES = {"all", "mr", "boll", "trend", "rs", "liquidation"}


class CredentialIn(BaseModel):
    engine: str = Field(default="all", description="Engine name or 'all'")
    is_testnet: bool = True
    api_key: str
    api_secret: str
    label: str = ""


class CredentialOut(BaseModel):
    id: int
    engine: str
    is_testnet: bool
    label: str

    model_config = ConfigDict(from_attributes=True)


@router.get("/", response_model=List[CredentialOut])
def list_credentials(
    db: Session = Depends(get_db), current_user=Depends(get_current_user)
):
    return (
        db.query(ApiCredential)
        .filter(ApiCredential.user_id == current_user.id)
        .order_by(ApiCredential.engine)
        .all()
    )


@router.post("/", response_model=CredentialOut, status_code=201)
def upsert_credential(
    payload: CredentialIn,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    engine = payload.engine.lower()
    if engine not in ALLOWED_ENGINES:
        raise HTTPException(status_code=400, detail="Unknown engine")

    existing = (
        db.query(ApiCredential)
        .filter(
            ApiCredential.user_id == current_user.id,
            ApiCredential.engine == engine,
            ApiCredential.is_testnet == payload.is_testnet,
        )
        .first()
    )

    if existing:
        existing.api_key = payload.api_key
        existing.api_secret = payload.api_secret
        existing.label = payload.label
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing

    cred = ApiCredential(
        user_id=current_user.id,
        engine=engine,
        is_testnet=payload.is_testnet,
        api_key=payload.api_key,
        api_secret=payload.api_secret,
        label=payload.label,
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    return cred


@router.delete("/{cred_id}", status_code=204)
def delete_credential(
    cred_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)
):
    cred = (
        db.query(ApiCredential)
        .filter(ApiCredential.user_id == current_user.id, ApiCredential.id == cred_id)
        .first()
    )
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")

    db.delete(cred)
    db.commit()
    return None


def resolve_credential(
    db: Session, user_id: int, engine: str, is_testnet: bool
) -> Optional[ApiCredential]:
    """Return the most specific credential for a user."""
    specific = (
        db.query(ApiCredential)
        .filter(
            ApiCredential.user_id == user_id,
            ApiCredential.engine == engine,
            ApiCredential.is_testnet == is_testnet,
        )
        .first()
    )
    if specific:
        return specific
    return (
        db.query(ApiCredential)
        .filter(
            ApiCredential.user_id == user_id,
            ApiCredential.engine == "all",
            ApiCredential.is_testnet == is_testnet,
        )
        .first()
    )
