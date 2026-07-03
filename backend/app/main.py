"""
Setu backend — FastAPI app entrypoint.

This is intentionally minimal right now (Phase A). Its only job at this
stage is to prove the app boots, responds, and can reach the database.
Real endpoints get added router by router in later phases (see
backend/app/routers/).
"""

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db

settings = get_settings()

app = FastAPI(
    title="Setu API",
    description="Backend for Setu — predictive, offline-first disaster alerting.",
    version="0.1.0",
)

# Allow the frontend (running on a different domain/port) to call this API.
# In Phase A this is wide open for local development; before the app goes
# live for judges, this should be tightened to the actual deployed
# frontend URL only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    """Basic liveness check — if this responds, the backend is up."""
    return {
        "service": "setu-backend",
        "status": "ok",
        "environment": settings.environment,
    }


@app.get("/health")
def health_check():
    """Separate health endpoint, useful for uptime monitoring later."""
    return {"status": "healthy"}


@app.get("/db-check")
def db_check(db: Session = Depends(get_db)):
    """
    TEMPORARY — Phase A only.

    Proves the backend can actually reach Supabase: runs the simplest
    possible query (SELECT 1) and reports success or failure. Delete
    this endpoint once Phase B adds real tables and real queries to
    prove connectivity through — it'll have served its purpose.
    """
    try:
        db.execute(text("SELECT 1"))
        return {"database": "connected"}
    except Exception as e:
        return {"database": "error", "detail": str(e)}


# Routers get included here as each phase builds them, e.g.:
# from app.routers import data
# app.include_router(data.router, prefix="/data", tags=["data"])