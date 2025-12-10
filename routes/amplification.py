from fastapi import APIRouter, HTTPException

from engines import amplification


router = APIRouter()


@router.get("/amplification/config")
def get_amplification_config():
    return amplification.get_config().dict()


@router.post("/amplification/config")
def set_amplification_config(payload: dict):
    try:
        cfg = amplification.set_config(payload)
    except Exception as exc:  # pragma: no cover - surfacing validation error
        raise HTTPException(status_code=400, detail=str(exc))
    return cfg.dict()


@router.get("/amplification/summary")
def amplification_summary():
    try:
        return amplification.summarize_amplification()
    except Exception as exc:  # pragma: no cover - surfacing fetch issues
        raise HTTPException(status_code=400, detail=str(exc))
