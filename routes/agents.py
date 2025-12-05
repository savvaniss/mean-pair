from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from database import AgentRun
from config import create_user_client
from security import get_current_user, get_db
from routes.credentials import resolve_credential

router = APIRouter(prefix="/agents", tags=["agents"])
ALLOWED_ENGINES = {"mr", "boll", "trend", "rs", "liquidation", "listing"}


class AgentRunRequest(BaseModel):
    engine: str
    is_testnet: bool = True


class AgentRunUpdate(BaseModel):
    status: str


class AgentRunOut(BaseModel):
    id: int
    engine: str
    status: str
    is_testnet: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


@router.get("/", response_model=List[AgentRunOut])
def list_agent_runs(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return (
        db.query(AgentRun)
        .filter(AgentRun.user_id == current_user.id)
        .order_by(AgentRun.created_at.desc())
        .all()
    )


@router.post("/run", response_model=AgentRunOut, status_code=201)
def register_agent_run(
    payload: AgentRunRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)
):
    if payload.engine not in ALLOWED_ENGINES:
        raise HTTPException(status_code=400, detail="Unknown engine for agent")
    cred = resolve_credential(db, current_user.id, payload.engine, payload.is_testnet)
    if not cred:
        raise HTTPException(
            status_code=400,
            detail="No API credential found for this engine and environment",
        )
    # instantiate client to ensure credentials are structurally valid
    try:
        create_user_client(cred.api_key, cred.api_secret, payload.is_testnet)
    except Exception as exc:  # pragma: no cover - thin validation
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    now = datetime.utcnow()
    run = AgentRun(
        user_id=current_user.id,
        engine=payload.engine,
        status="running",
        is_testnet=payload.is_testnet,
        created_at=now,
        updated_at=now,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


@router.patch("/{run_id}", response_model=AgentRunOut)
def update_agent_run(
    run_id: int,
    payload: AgentRunUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    run = (
        db.query(AgentRun)
        .filter(AgentRun.user_id == current_user.id, AgentRun.id == run_id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Agent run not found")
    run.status = payload.status
    run.updated_at = datetime.utcnow()
    db.add(run)
    db.commit()
    db.refresh(run)
    return run
