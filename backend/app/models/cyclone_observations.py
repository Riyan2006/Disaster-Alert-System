"""
Setu backend — cyclone observations model.

Deliberately separate from Reading: a storm is a moving object tracked
by its own ID over time, not a value at a fixed city coordinate, so it
doesn't fit the city-keyed shape the rest of readings.py uses.

This is an early, opportunistic addition (see build discussion) — full
GDACS integration is really a Phase E (Cyclone model) task, but since
polling for "is there an active storm right now" costs almost nothing,
we start capturing real storms as they happen now rather than only
starting the clock when Phase E begins.
"""

from sqlalchemy import Column, Integer, Float, String, DateTime, Text, Index
from sqlalchemy.sql import func

from app.database import Base


class CycloneObservation(Base):
    __tablename__ = "cyclone_observations"

    id = Column(Integer, primary_key=True, index=True)

    # GDACS's own event identifier — lets us track the same storm across
    # multiple polling cycles as it moves.
    storm_id = Column(String(50), nullable=False, index=True)
    storm_name = Column(String(100), nullable=True)

    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    alert_level = Column(String(20), nullable=True)  # e.g. "Green", "Orange", "Red"
    wind_speed_kmh = Column(Float, nullable=True)
    category = Column(String(), nullable=True)

    # GDACS's severity info often arrives as descriptive text rather than
    # a single clean field (this is a real limitation of their public
    # data, not something we can fix on our end) — stored as a fallback
    # so nothing is silently lost, and so we can refine parsing later
    # once we've seen enough real examples.
    raw_severity_text = Column(Text, nullable=True)

    recorded_at = Column(DateTime(timezone=True), nullable=False, index=True)
    fetched_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_storm_recorded", "storm_id", "recorded_at"),
    )

    def __repr__(self):
        return f"<CycloneObservation(storm={self.storm_name}, at={self.recorded_at})>"