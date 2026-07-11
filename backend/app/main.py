"""
Setu backend — FastAPI app entrypoint.

Phase B: creates database tables on startup, starts the recurring
data-polling loop in the background, and exposes the /data router
for the frontend to query.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import engine, Base
from app.models.readings import Reading  # noqa: F401 — import ensures the table is registered
from app.models.cyclone_observations import CycloneObservation  # noqa: F401 — same reason
from app.models.station_readings import CityStationReading  # noqa: F401 — same reason
from app.services.ingestion import poll_and_store_loop
from app.routers import data as data_router

settings = get_settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Creates the `readings` table if it doesn't exist yet — safe to run
    # every startup, it's a no-op once the table already exists.
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables ready.")

    polling_task = asyncio.create_task(poll_and_store_loop())
    logger.info("Background polling task started.")

    yield

    polling_task.cancel()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass
    logger.info("Setu backend shutting down.")


app = FastAPI(
    title="Setu API",
    description="Backend for Setu — predictive, offline-first disaster alerting.",
    version="0.1.0",
    lifespan=lifespan,
)

# TODO once the Vercel URL is final: replace "*" with the exact deployed
# frontend URL for a tighter, production-appropriate CORS policy.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"service": "setu-backend", "status": "ok", "environment": settings.environment}


@app.get("/health")
def health_check():
    return {"status": "healthy"}


app.include_router(data_router.router, prefix="/api/data", tags=["data"])