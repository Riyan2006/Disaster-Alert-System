"""
Setu backend — readings model.

Stores both AQI and weather readings, one row per reading, keyed by
city and timestamp. This is the foundation table every later phase
(models, dashboard, replay) queries against.
"""

import enum

from sqlalchemy import Column, Integer, Float, String, DateTime, Index
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.sql import func

from app.database import Base


class ReadingType(str, enum.Enum):
    AQI = "aqi"
    WEATHER = "weather"


class Reading(Base):
    __tablename__ = "readings"

    id = Column(Integer, primary_key=True, index=True)

    # Location
    city = Column(String(50), nullable=False, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)

    reading_type = Column(SQLEnum(ReadingType), nullable=False, index=True)

    # Which live data source this came from, e.g. "openaq", "aqicn",
    # "openweathermap", "openmeteo" — useful for debugging and for
    # showing data provenance later.
    source = Column(String(50), nullable=False)

    recorded_at = Column(DateTime(timezone=True), nullable=False, index=True)
    fetched_at = Column(DateTime(timezone=True), server_default=func.now())

    # AQI fields — null on weather rows
    aqi_value = Column(Integer, nullable=True)
    aqi_pm25 = Column(Float, nullable=True)
    aqi_pm10 = Column(Float, nullable=True)
    aqi_no2 = Column(Float, nullable=True)
    aqi_so2 = Column(Float, nullable=True)
    aqi_co = Column(Float, nullable=True)
    aqi_o3 = Column(Float, nullable=True)

    # Weather fields — null on AQI rows
    weather_temp = Column(Float, nullable=True)            # Celsius
    weather_humidity = Column(Float, nullable=True)        # %
    weather_wind_speed = Column(Float, nullable=True)      # m/s
    weather_wind_direction = Column(Float, nullable=True)  # degrees
    weather_pressure = Column(Float, nullable=True)        # hPa
    weather_precipitation = Column(Float, nullable=True)   # mm
    weather_visibility = Column(Float, nullable=True)      # km
    weather_cloud_cover = Column(Float, nullable=True)     # %

    __table_args__ = (
        Index("idx_city_type_recorded", "city", "reading_type", "recorded_at"),
        Index("idx_city_recorded", "city", "recorded_at"),
    )

    def __repr__(self):
        return f"<Reading(city={self.city}, type={self.reading_type}, at={self.recorded_at})>"