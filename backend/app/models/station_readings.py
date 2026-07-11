"""
Setu backend — city station readings model.

Distinct from Reading: this is one row PER MONITORING STATION within a
city, not one row per city. A city like Kolkata can have 40+ real
stations (Ballygunge, Tangra, Bidhannagar, etc.) — this table lets the
frontend show every one of them, not just a single city-level number.
"""

from sqlalchemy import Column, Integer, Float, String, DateTime, Index
from sqlalchemy.sql import func

from app.database import Base


class CityStationReading(Base):
    __tablename__ = "city_station_readings"

    id = Column(Integer, primary_key=True, index=True)

    # Which of our covered metro cities this station belongs to.
    city = Column(String(50), nullable=False, index=True)

    # AQICN's own station identifier — lets us update the same station's
    # row cleanly across polling cycles instead of accumulating duplicates.
    station_uid = Column(String(50), nullable=False, index=True)
    station_name = Column(String(200), nullable=True)

    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)

    # AQICN's own composite AQI for this specific station.
    aqi_value = Column(Integer, nullable=True)

    # Per-pollutant sub-index values from AQICN's iaqi block. Same caveat
    # as the main Reading table: these are AQI sub-indices, not raw
    # µg/m³ concentrations (confirmed directly against aqicn.org's own
    # station pages, which label this number "AQI", not a concentration
    # unit). Fine for this browse-by-station feature, which is about
    # showing hyperlocal AQI variation, not feeding the ML model.
    pm25 = Column(Float, nullable=True)
    pm10 = Column(Float, nullable=True)
    no2 = Column(Float, nullable=True)
    so2 = Column(Float, nullable=True)
    co = Column(Float, nullable=True)
    o3 = Column(Float, nullable=True)

    recorded_at = Column(DateTime(timezone=True), nullable=False, index=True)
    fetched_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_city_station", "city", "station_uid"),
    )

    def __repr__(self):
        return f"<CityStationReading(city={self.city}, station={self.station_name})>"