"""
Setu backend — FastAPI app entrypoint.

This is intentionally minimal right now (Phase A). Its only job at this
stage is to prove the app boots and responds. Real endpoints get added
router by router in later phases (see backend/app/routers/).
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

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


# Routers get included here as each phase builds them, e.g.:
# from app.routers import data
# app.include_router(data.router, prefix="/data", tags=["data"])
