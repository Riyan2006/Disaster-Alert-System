"""
Setu backend — data ingestion service.

One function per live data source, each returning a list of
normalized reading dicts ready to be stored as Reading rows.
Also holds the recurring polling loop that keeps the database fresh.

A note on OpenAQ specifically: their API is v3 and requires an
X-API-Key header (not a "token" query param — that was the older v2
style). v3 is also location-based rather than city-based, so instead
of hardcoding station IDs (which drift out of date and differ per
city), we look up the nearest station to each city's coordinates at
request time.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Covered metro cities — matches the build plan's "trained model coverage"
# list. Add more cities here later without touching any other file.
CITY_CONFIGS: Dict[str, Dict[str, Any]] = {
    "delhi": {"name": "Delhi", "latitude": 28.6139, "longitude": 77.2090},
    "mumbai": {"name": "Mumbai", "latitude": 19.0760, "longitude": 72.8777},
    "bangalore": {"name": "Bangalore", "latitude": 12.9716, "longitude": 77.5946},
    "chennai": {"name": "Chennai", "latitude": 13.0827, "longitude": 80.2707},
    "kolkata": {"name": "Kolkata", "latitude": 22.5726, "longitude": 88.3639},
    "hyderabad": {"name": "Hyderabad", "latitude": 17.3850, "longitude": 78.4867},
}


# =============================================================================
# OPENAQ (v3) — AQI data
# =============================================================================
async def fetch_openaq(city_key: str, api_key: Optional[str]) -> List[Dict[str, Any]]:
    """
    OpenAQ's /v3/locations endpoint has no way to sort by distance or by
    recency — it just returns "a" set of nearby stations in an
    unspecified order. Trusting result #1 blindly can hand you a station
    that's technically within the radius but has been dead for years
    (this is exactly what happened with Chennai/Kolkata during testing —
    both resolved to stations last active in 2017/2018).

    So instead: fetch several candidates, then check each one's actual
    latest data ourselves, and use the first one that's genuinely fresh
    (reported within the last few days). This is slower (up to a few
    extra requests) but actually correct.
    """
    config = CITY_CONFIGS.get(city_key)
    if not config or not api_key:
        return []

    headers = {"X-API-Key": api_key}
    freshness_cutoff = datetime.now(timezone.utc) - timedelta(days=3)

    try:
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            loc_resp = await client.get(
                "https://api.openaq.org/v3/locations",
                params={
                    "coordinates": f"{config['latitude']},{config['longitude']}",
                    "radius": 25000,
                    "limit": 10,  # check up to 10 candidates, not just 1
                },
            )
            loc_resp.raise_for_status()
            candidates = loc_resp.json().get("results", [])

            if not candidates:
                logger.info(f"OpenAQ: no stations at all near {city_key}")
                return []

            for candidate in candidates:
                location_id = candidate["id"]

                latest_resp = await client.get(
                    f"https://api.openaq.org/v3/locations/{location_id}/latest"
                )
                if latest_resp.status_code != 200:
                    continue
                results = latest_resp.json().get("results", [])
                if not results:
                    continue

                # Find this station's most recent reading across all its sensors
                timestamps = [
                    datetime.fromisoformat(r["datetime"]["utc"].replace("Z", "+00:00"))
                    for r in results
                    if r.get("datetime", {}).get("utc")
                ]
                if not timestamps:
                    continue
                most_recent = max(timestamps)

                if most_recent < freshness_cutoff:
                    logger.info(
                        f"OpenAQ: skipping stale station {location_id} for {city_key} "
                        f"(last reported {most_recent.date()})"
                    )
                    continue

                # Found a genuinely live station — parse and use it
                pm25 = pm10 = no2 = so2 = co = o3 = None
                for entry in results:
                    param_name = (entry.get("parameter") or {}).get("name", "").lower()
                    value = entry.get("value")
                    if param_name == "pm25":
                        pm25 = value
                    elif param_name == "pm10":
                        pm10 = value
                    elif param_name == "no2":
                        no2 = value
                    elif param_name == "so2":
                        so2 = value
                    elif param_name == "co":
                        co = value
                    elif param_name == "o3":
                        o3 = value

                return [{
                    "city": city_key,
                    "latitude": config["latitude"],
                    "longitude": config["longitude"],
                    "reading_type": "aqi",
                    "source": "openaq",
                    "recorded_at": most_recent,
                    "aqi_value": calculate_aqi_from_pm25(pm25) if pm25 is not None else None,
                    "aqi_pm25": pm25,
                    "aqi_pm10": pm10,
                    "aqi_no2": no2,
                    "aqi_so2": so2,
                    "aqi_co": co,
                    "aqi_o3": o3,
                }]

            # Every candidate was stale or empty
            logger.info(f"OpenAQ: no live station found near {city_key} among {len(candidates)} candidates")
            return []

    except httpx.HTTPStatusError as e:
        logger.error(f"OpenAQ HTTP error for {city_key}: {e.response.status_code}")
    except Exception as e:
        logger.error(f"OpenAQ error for {city_key}: {e}")

    return []


def calculate_aqi_from_pm25(pm25: float) -> int:
    """Simplified US EPA PM2.5 -> AQI conversion, used when a source
    gives raw pollutant concentrations but not a pre-computed AQI."""
    breakpoints = [
        (0, 12.0, 0, 50),
        (12.1, 35.4, 51, 100),
        (35.5, 55.4, 101, 150),
        (55.5, 150.4, 151, 200),
        (150.5, 250.4, 201, 300),
        (250.5, 500.4, 301, 500),
    ]
    for c_low, c_high, i_low, i_high in breakpoints:
        if c_low <= pm25 <= c_high:
            return int(round(((i_high - i_low) / (c_high - c_low)) * (pm25 - c_low) + i_low))
    return 500


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance between two lat/lon points, in km."""
    import math
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return r * 2 * math.asin(math.sqrt(a))


# =============================================================================
# AQICN / WAQI — AQI data (geo-based, so it works for any city without
# needing to guess how AQICN spells each city's name internally)
# =============================================================================
async def fetch_aqicn(city_key: str, api_token: Optional[str]) -> List[Dict[str, Any]]:
    config = CITY_CONFIGS.get(city_key)
    if not config or not api_token:
        return []

    readings: List[Dict[str, Any]] = []

    try:
        url = f"https://api.waqi.info/feed/geo:{config['latitude']};{config['longitude']}/"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params={"token": api_token})
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "ok":
            logger.warning(f"AQICN non-ok status for {city_key}: {data.get('data')}")
            return []

        feed = data["data"]
        logger.info(f"AQICN raw city block for {city_key}: {feed.get('city')}")
        iaqi = feed.get("iaqi", {})

        # Diagnostic + correctness check: AQICN tells us which real
        # station it actually matched via feed["city"]["name"] and its
        # true coordinates via feed["city"]["geo"]. If that station is
        # implausibly far from the city we asked about, something's
        # wrong (bad geo match, or no real nearby station exists) — log
        # it clearly and reject the reading rather than silently storing
        # a wrong city's data under this city's name.
        station_info = feed.get("city", {})
        station_name = station_info.get("name", "unknown")
        station_geo = station_info.get("geo")

        if station_geo and len(station_geo) == 2:
            distance_km = _haversine_km(
                config["latitude"], config["longitude"], station_geo[0], station_geo[1]
            )
            logger.info(
                f"AQICN for {city_key}: matched station '{station_name}' "
                f"({distance_km:.0f} km away)"
            )
            if distance_km > 75:
                logger.warning(
                    f"AQICN: rejecting match for {city_key} — matched station "
                    f"'{station_name}' is {distance_km:.0f} km away, too far to trust"
                )
                return []
        else:
            # No geo returned means we have no way to verify this match is
            # actually nearby — this used to be silently accepted, which is
            # exactly how a bad cross-city match slipped through undetected.
            # Failing closed (rejecting) here is the safer default: better
            # to have no data for a cycle than silently wrong data.
            logger.warning(
                f"AQICN for {city_key}: matched station '{station_name}' but no geo "
                f"returned to verify distance — rejecting, can't confirm it's real"
            )
            return []

        time_iso = (feed.get("time") or {}).get("iso")
        recorded_at = (
            datetime.fromisoformat(time_iso) if time_iso else datetime.now(timezone.utc)
        )

        def val(key: str) -> Optional[float]:
            item = iaqi.get(key)
            return float(item["v"]) if item and item.get("v") is not None else None

        readings.append({
            "city": city_key,
            "latitude": config["latitude"],
            "longitude": config["longitude"],
            "reading_type": "aqi",
            "source": "aqicn",
            "recorded_at": recorded_at,
            "aqi_value": feed.get("aqi") if isinstance(feed.get("aqi"), int) else None,
            "aqi_pm25": val("pm25"),
            "aqi_pm10": val("pm10"),
            "aqi_no2": val("no2"),
            "aqi_so2": val("so2"),
            "aqi_co": val("co"),
            "aqi_o3": val("o3"),
        })

    except httpx.HTTPStatusError as e:
        logger.error(f"AQICN HTTP error for {city_key}: {e.response.status_code}")
    except Exception as e:
        logger.error(f"AQICN error for {city_key}: {e}")

    return readings


# =============================================================================
# OPENWEATHERMAP — weather data
# =============================================================================
async def fetch_openweathermap(city_key: str, api_key: Optional[str]) -> List[Dict[str, Any]]:
    config = CITY_CONFIGS.get(city_key)
    if not config or not api_key:
        return []

    readings: List[Dict[str, Any]] = []

    try:
        params = {
            "lat": config["latitude"],
            "lon": config["longitude"],
            "appid": api_key,
            "units": "metric",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                "https://api.openweathermap.org/data/2.5/weather", params=params
            )
            resp.raise_for_status()
            data = resp.json()

        main = data.get("main", {})
        wind = data.get("wind", {})
        clouds = data.get("clouds", {})
        visibility = data.get("visibility")  # meters

        readings.append({
            "city": city_key,
            "latitude": config["latitude"],
            "longitude": config["longitude"],
            "reading_type": "weather",
            "source": "openweathermap",
            "recorded_at": datetime.fromtimestamp(data["dt"], tz=timezone.utc),
            "weather_temp": main.get("temp"),
            "weather_humidity": main.get("humidity"),
            "weather_wind_speed": wind.get("speed"),
            "weather_wind_direction": wind.get("deg"),
            "weather_pressure": main.get("pressure"),
            "weather_precipitation": data.get("rain", {}).get("1h", 0) or data.get("snow", {}).get("1h", 0) or None,
            "weather_visibility": visibility / 1000 if visibility else None,
            "weather_cloud_cover": clouds.get("all"),
        })

    except httpx.HTTPStatusError as e:
        logger.error(f"OpenWeatherMap HTTP error for {city_key}: {e.response.status_code}")
    except Exception as e:
        logger.error(f"OpenWeatherMap error for {city_key}: {e}")

    return readings


# =============================================================================
# OPEN-METEO — weather data, no key required
# =============================================================================
async def fetch_openmeteo(city_key: str) -> List[Dict[str, Any]]:
    config = CITY_CONFIGS.get(city_key)
    if not config:
        return []

    readings: List[Dict[str, Any]] = []

    try:
        params = {
            "latitude": config["latitude"],
            "longitude": config["longitude"],
            "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,"
                       "wind_direction_10m,surface_pressure,precipitation,cloud_cover",
            "timezone": "auto",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
            resp.raise_for_status()
            data = resp.json()

        current = data.get("current", {})
        time_str = current.get("time")
        if not time_str:
            return []

        readings.append({
            "city": city_key,
            "latitude": config["latitude"],
            "longitude": config["longitude"],
            "reading_type": "weather",
            "source": "openmeteo",
            "recorded_at": datetime.fromisoformat(time_str),
            "weather_temp": current.get("temperature_2m"),
            "weather_humidity": current.get("relative_humidity_2m"),
            "weather_wind_speed": current.get("wind_speed_10m"),
            "weather_wind_direction": current.get("wind_direction_10m"),
            "weather_pressure": current.get("surface_pressure"),
            "weather_precipitation": current.get("precipitation"),
            "weather_visibility": None,
            "weather_cloud_cover": current.get("cloud_cover"),
        })

    except httpx.HTTPStatusError as e:
        logger.error(f"Open-Meteo HTTP error for {city_key}: {e.response.status_code}")
    except Exception as e:
        logger.error(f"Open-Meteo error for {city_key}: {e}")

    return readings


# =============================================================================
# AGGREGATE FETCHERS
# =============================================================================
async def fetch_all_for_city(
    city_key: str,
    openaq_key: Optional[str] = None,
    aqicn_token: Optional[str] = None,
    openweathermap_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    tasks = [
        fetch_openaq(city_key, openaq_key),
        fetch_aqicn(city_key, aqicn_token),
        fetch_openmeteo(city_key),
        fetch_openweathermap(city_key, openweathermap_key),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_readings: List[Dict[str, Any]] = []
    for result in results:
        if isinstance(result, list):
            all_readings.extend(result)
        elif isinstance(result, Exception):
            logger.error(f"Fetch task failed: {result}")
    return all_readings


async def fetch_all_cities(
    openaq_key: Optional[str] = None,
    aqicn_token: Optional[str] = None,
    openweathermap_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    all_readings: List[Dict[str, Any]] = []
    for city_key in CITY_CONFIGS:
        all_readings.extend(
            await fetch_all_for_city(city_key, openaq_key, aqicn_token, openweathermap_key)
        )
    return all_readings


# =============================================================================
# RECURRING POLLING LOOP
# =============================================================================
POLLING_INTERVAL_SECONDS = 300  # 5 minutes


async def poll_and_store_loop():
    """
    Runs forever (until the app shuts down): fetch all cities, store
    whatever came back, wait, repeat. Started from main.py's lifespan
    handler on app startup.
    """
    # Imported here, not at module top, to avoid a circular import
    # between ingestion.py and database.py/models at startup.
    from app.database import SessionLocal
    from app.models.readings import Reading
    from app.config import get_settings

    settings = get_settings()

    while True:
        try:
            logger.info("Polling cycle starting...")
            readings_data = await fetch_all_cities(
                openaq_key=settings.openaq_api_key,
                aqicn_token=settings.aqicn_api_token,
                openweathermap_key=settings.openweathermap_api_key,
            )

            if readings_data:
                db = SessionLocal()
                try:
                    for rd in readings_data:
                        db.add(Reading(**rd))
                    db.commit()
                    logger.info(f"Stored {len(readings_data)} readings")
                except Exception as e:
                    db.rollback()
                    logger.error(f"DB error storing readings: {e}")
                finally:
                    db.close()
            else:
                logger.warning("No readings fetched this cycle")

        except Exception as e:
            logger.error(f"Polling cycle error: {e}")

        await asyncio.sleep(POLLING_INTERVAL_SECONDS)