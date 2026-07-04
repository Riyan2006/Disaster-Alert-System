"""
Setu backend — data endpoints.

Serves live and historical readings to the frontend. Everything here
is a read-only query over what services/ingestion.py's polling loop
has already stored.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends
from sqlalchemy.orm import Session
from sqlalchemy import desc, and_

from app.database import get_db
from app.models.readings import Reading, ReadingType
from app.services.ingestion import CITY_CONFIGS

router = APIRouter()


def _serialize_current(city_key: str, db: Session) -> dict:
    latest_aqi = (
        db.query(Reading)
        .filter(and_(Reading.city == city_key, Reading.reading_type == ReadingType.AQI))
        .order_by(desc(Reading.recorded_at))
        .first()
    )
    latest_weather = (
        db.query(Reading)
        .filter(and_(Reading.city == city_key, Reading.reading_type == ReadingType.WEATHER))
        .order_by(desc(Reading.recorded_at))
        .first()
    )

    config = CITY_CONFIGS[city_key]

    return {
        "city": city_key,
        "city_name": config["name"],
        "latitude": config["latitude"],
        "longitude": config["longitude"],
        "aqi": None if not latest_aqi else {
            "value": latest_aqi.aqi_value,
            "pm25": latest_aqi.aqi_pm25,
            "pm10": latest_aqi.aqi_pm10,
            "no2": latest_aqi.aqi_no2,
            "so2": latest_aqi.aqi_so2,
            "co": latest_aqi.aqi_co,
            "o3": latest_aqi.aqi_o3,
            "source": latest_aqi.source,
            "recorded_at": latest_aqi.recorded_at.isoformat(),
        },
        "weather": None if not latest_weather else {
            "temp": latest_weather.weather_temp,
            "humidity": latest_weather.weather_humidity,
            "wind_speed": latest_weather.weather_wind_speed,
            "wind_direction": latest_weather.weather_wind_direction,
            "pressure": latest_weather.weather_pressure,
            "precipitation": latest_weather.weather_precipitation,
            "visibility": latest_weather.weather_visibility,
            "cloud_cover": latest_weather.weather_cloud_cover,
            "source": latest_weather.source,
            "recorded_at": latest_weather.recorded_at.isoformat(),
        },
    }


@router.get("/current/{city_key}")
def get_current_readings(city_key: str, db: Session = Depends(get_db)):
    if city_key not in CITY_CONFIGS:
        raise HTTPException(status_code=404, detail=f"Unknown city: {city_key}")
    return _serialize_current(city_key, db)


@router.get("/current")
def get_all_cities_current(db: Session = Depends(get_db)):
    return [_serialize_current(city_key, db) for city_key in CITY_CONFIGS]


@router.get("/history/{city_key}")
def get_historical_readings(
    city_key: str,
    reading_type: Optional[str] = Query(None, description="'aqi' or 'weather'"),
    hours: int = Query(24, ge=1, le=720),
    limit: int = Query(1000, ge=1, le=10000),
    db: Session = Depends(get_db),
):
    if city_key not in CITY_CONFIGS:
        raise HTTPException(status_code=404, detail=f"Unknown city: {city_key}")

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = db.query(Reading).filter(
        and_(Reading.city == city_key, Reading.recorded_at >= since)
    )

    if reading_type:
        try:
            query = query.filter(Reading.reading_type == ReadingType(reading_type))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid reading_type: {reading_type}")

    readings = query.order_by(desc(Reading.recorded_at)).limit(limit).all()

    return {
        "city": city_key,
        "count": len(readings),
        "readings": [
            {
                "id": r.id,
                "reading_type": r.reading_type.value,
                "source": r.source,
                "recorded_at": r.recorded_at.isoformat(),
                "aqi_value": r.aqi_value,
                "aqi_pm25": r.aqi_pm25,
                "weather_temp": r.weather_temp,
                "weather_humidity": r.weather_humidity,
            }
            for r in readings
        ],
    }


@router.get("/timeseries/{city_key}")
def get_aqi_timeseries(
    city_key: str,
    hours: int = Query(48, ge=1, le=168),
    db: Session = Depends(get_db),
):
    if city_key not in CITY_CONFIGS:
        raise HTTPException(status_code=404, detail=f"Unknown city: {city_key}")

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    readings = (
        db.query(Reading)
        .filter(and_(
            Reading.city == city_key,
            Reading.reading_type == ReadingType.AQI,
            Reading.recorded_at >= since,
            Reading.aqi_value.isnot(None),
        ))
        .order_by(Reading.recorded_at.asc())
        .all()
    )

    return {
        "city": city_key,
        "timeseries": [
            {"time": r.recorded_at.isoformat(), "value": r.aqi_value} for r in readings
        ],
    }


@router.get("/cities")
def get_covered_cities():
    return {
        "cities": [
            {"key": key, "name": cfg["name"], "latitude": cfg["latitude"], "longitude": cfg["longitude"]}
            for key, cfg in CITY_CONFIGS.items()
        ]
    }