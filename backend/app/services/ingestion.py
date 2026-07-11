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


class _RateLimiter:
    """
    Enforces a minimum spacing between calls, shared across every caller
    that awaits it — not just "hope concurrency happens to be low
    enough." OpenAQ's documented free-tier limit is 60 requests/minute;
    without this, firing dozens of station-detail requests concurrently
    (which asyncio.gather does by default) blows straight through that
    in a fraction of a second, exactly what caused the wave of 429s
    across Mumbai/Bangalore/Hyderabad. min_interval=1.1s keeps sustained
    throughput at ~54/min, safely under the real limit with margin.
    """

    def __init__(self, min_interval: float):
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def wait(self):
        async with self._lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_call
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_call = asyncio.get_event_loop().time()


# Shared across ALL OpenAQ calls in this module — fetch_openaq and
# fetch_city_stations_openaq both draw from the same 60/min quota, so
# they need to share one limiter, not have their own separate ones.
_openaq_rate_limiter = _RateLimiter(min_interval=1.1)

# Covered metro cities — matches the build plan's "trained model coverage"
# list. Add more cities here later without touching any other file.
CITY_CONFIGS: Dict[str, Dict[str, Any]] = {
    # bbox = (south_lat, west_lon, north_lat, east_lon) — a rough box
    # covering the metro area, used for AQICN's map/bounds endpoint to
    # discover every monitoring station within the city, not just the
    # single nearest one.
    "delhi": {"name": "Delhi", "latitude": 28.6139, "longitude": 77.2090,
              "bbox": (28.40, 76.80, 28.90, 77.40)},
    "mumbai": {"name": "Mumbai", "latitude": 19.0760, "longitude": 72.8777,
               "bbox": (18.90, 72.70, 19.30, 73.05)},
    "bangalore": {"name": "Bangalore", "latitude": 12.9716, "longitude": 77.5946,
                  "bbox": (12.80, 77.40, 13.20, 77.80)},
    "chennai": {"name": "Chennai", "latitude": 13.0827, "longitude": 80.2707,
                "bbox": (12.90, 80.10, 13.30, 80.35)},
    "kolkata": {"name": "Kolkata", "latitude": 22.5726, "longitude": 88.3639,
                "bbox": (22.40, 88.20, 22.70, 88.50)},
    "hyderabad": {"name": "Hyderabad", "latitude": 17.3850, "longitude": 78.4867,
                  "bbox": (17.20, 78.30, 17.60, 78.65)},
}


# =============================================================================
# OPENAQ (v3) — AQI data
# =============================================================================
async def fetch_openaq(city_key: str, api_key: Optional[str]) -> List[Dict[str, Any]]:
    """
    OpenAQ v3's /locations/{id}/latest endpoint gives each reading's value
    plus a sensorsId — not a parameter name directly. To know what a
    sensorsId measures, cross-reference it against that same location's
    own sensor list (each sensor has an id and a parameter.name).

    Evaluates every non-stale candidate station (not just the first
    usable one) and picks whichever has the MOST complete pollutant
    coverage — a station reporting only PM2.5 shouldn't beat one nearby
    reporting all six pollutants just because it happened to be checked
    first. Recency is used only to break ties between equally-complete
    candidates.
    """
    config = CITY_CONFIGS.get(city_key)
    if not config or not api_key:
        return []

    headers = {"X-API-Key": api_key}
    freshness_cutoff = datetime.now(timezone.utc) - timedelta(days=3)

    best_reading: Optional[Dict[str, Any]] = None
    best_completeness = -1
    best_recency: Optional[datetime] = None

    try:
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            await _openaq_rate_limiter.wait()
            loc_resp = await client.get(
                "https://api.openaq.org/v3/locations",
                params={
                    "coordinates": f"{config['latitude']},{config['longitude']}",
                    "radius": 25000,
                    "limit": 10,
                },
            )
            loc_resp.raise_for_status()
            candidates = loc_resp.json().get("results", [])

            if not candidates:
                logger.info(f"OpenAQ: no stations at all near {city_key}")
                return []

            def _last_reported_str(loc):
                return (loc.get("datetimeLast") or {}).get("utc") or ""

            candidates.sort(key=_last_reported_str, reverse=True)

            for candidate in candidates:
                location_id = candidate["id"]

                last_reported_str = _last_reported_str(candidate)
                if last_reported_str:
                    last_reported_dt = datetime.fromisoformat(
                        last_reported_str.replace("Z", "+00:00")
                    )
                    if last_reported_dt < freshness_cutoff:
                        logger.info(
                            f"OpenAQ: skipping stale station {location_id} for "
                            f"{city_key} (last reported {last_reported_dt.date()})"
                        )
                        continue

                sensor_param_map = {
                    s["id"]: (s.get("parameter") or {}).get("name", "").lower()
                    for s in candidate.get("sensors", [])
                }

                await _openaq_rate_limiter.wait()
                latest_resp = await client.get(
                    f"https://api.openaq.org/v3/locations/{location_id}/latest"
                )
                if latest_resp.status_code != 200:
                    continue
                results = latest_resp.json().get("results", [])
                if not results:
                    continue

                pm25 = pm10 = no2 = so2 = co = o3 = None
                most_recent = None

                for entry in results:
                    param_name = sensor_param_map.get(entry.get("sensorsId"), "")
                    value = entry.get("value")

                    dt_str = (entry.get("datetime") or {}).get("utc")
                    if dt_str:
                        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                        if most_recent is None or dt > most_recent:
                            most_recent = dt

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

                values = (pm25, pm10, no2, so2, co, o3)
                completeness = sum(1 for v in values if v is not None)

                if completeness == 0:
                    logger.info(
                        f"OpenAQ: station {location_id} for {city_key} has no "
                        f"usable pollutant values, skipping"
                    )
                    continue

                logger.info(
                    f"OpenAQ: station {location_id} for {city_key} has "
                    f"{completeness}/6 pollutant fields populated"
                )

                is_better = (
                    completeness > best_completeness
                    or (
                        completeness == best_completeness
                        and most_recent is not None
                        and (best_recency is None or most_recent > best_recency)
                    )
                )
                if is_better:
                    best_completeness = completeness
                    best_recency = most_recent
                    best_reading = {
                        "city": city_key,
                        "latitude": config["latitude"],
                        "longitude": config["longitude"],
                        "reading_type": "aqi",
                        "source": "openaq",
                        "recorded_at": most_recent or datetime.now(timezone.utc),
                        "aqi_value": calculate_aqi_from_pm25(pm25) if pm25 is not None else None,
                        "aqi_pm25": pm25,
                        "aqi_pm10": pm10,
                        "aqi_no2": no2,
                        "aqi_so2": so2,
                        "aqi_co": co,
                        "aqi_o3": o3,
                    }

                # A station with all 6 fields is as complete as it gets —
                # no need to keep checking further candidates.
                if best_completeness == 6:
                    break

        if best_reading:
            logger.info(
                f"OpenAQ: using best station for {city_key} — "
                f"{best_completeness}/6 pollutant fields"
            )
            return [best_reading]

        logger.info(
            f"OpenAQ: no live station found near {city_key} among "
            f"{len(candidates)} candidates"
        )
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
def _parse_aqicn_feed(
    feed: Dict[str, Any], config: Dict[str, Any], city_key: str, method: str
) -> Optional[Dict[str, Any]]:
    """
    Shared parsing + distance-verification logic for an AQICN feed
    response, regardless of whether it came from the geo: endpoint or
    the keyword search endpoint. Returns a reading dict, or None if the
    match should be rejected (too far, or unverifiable).
    """
    station_info = feed.get("city", {})
    station_name = station_info.get("name", "unknown")
    station_geo = station_info.get("geo")

    if station_geo and len(station_geo) == 2:
        distance_km = _haversine_km(
            config["latitude"], config["longitude"], station_geo[0], station_geo[1]
        )
        logger.info(
            f"AQICN ({method}) for {city_key}: matched station '{station_name}' "
            f"({distance_km:.0f} km away)"
        )
        if distance_km > 75:
            logger.warning(
                f"AQICN ({method}): rejecting match for {city_key} — matched "
                f"station '{station_name}' is {distance_km:.0f} km away"
            )
            return None
    else:
        logger.warning(
            f"AQICN ({method}) for {city_key}: matched station '{station_name}' "
            f"but no geo returned to verify distance — rejecting"
        )
        return None

    iaqi = feed.get("iaqi", {})
    time_iso = (feed.get("time") or {}).get("iso")
    recorded_at = (
        datetime.fromisoformat(time_iso) if time_iso else datetime.now(timezone.utc)
    )

    # IMPORTANT: AQICN's "iaqi" object stands for "individual AQI" — these
    # per-pollutant numbers are already-converted AQI sub-indices (US EPA
    # scale, per AQICN's own data-platform documentation), NOT raw µg/m³
    # concentrations. OpenAQ's per-pollutant fields ARE raw concentrations.
    # Storing AQICN's sub-indices in the same aqi_pm25/aqi_pm10/etc columns
    # would silently mix two different units depending on which source
    # populated a given row — exactly the kind of thing that corrupts a
    # model trained on this data later without anyone noticing. So: keep
    # AQICN's correctly-computed overall aqi_value, but leave the
    # per-pollutant breakdown columns null for this source rather than
    # populate them with the wrong unit.
    return {
        "city": city_key,
        "latitude": config["latitude"],
        "longitude": config["longitude"],
        "reading_type": "aqi",
        "source": "aqicn",
        "recorded_at": recorded_at,
        "aqi_value": feed.get("aqi") if isinstance(feed.get("aqi"), int) else None,
        "aqi_pm25": None,
        "aqi_pm10": None,
        "aqi_no2": None,
        "aqi_so2": None,
        "aqi_co": None,
        "aqi_o3": None,
    }


async def fetch_aqicn(city_key: str, api_token: Optional[str]) -> List[Dict[str, Any]]:
    """
    Tries two independent, both-documented AQICN methods:
      1. geo:{lat};{lng} — direct coordinate lookup
      2. /search/?keyword={city name} — name-based station search

    Geo lookup was consistently resolving non-Delhi cities to a Delhi-area
    station regardless of real distance, which the distance check now
    catches and rejects — but rejecting isn't the same as finding real
    data. Keyword search is a genuinely different, independently
    documented lookup path, so it gets a real second attempt here rather
    than assuming geo's failure means no data exists at all.
    """
    config = CITY_CONFIGS.get(city_key)
    if not config or not api_token:
        return []

    # --- Attempt 1: geo lookup ---
    try:
        url = f"https://api.waqi.info/feed/geo:{config['latitude']};{config['longitude']}/"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params={"token": api_token})
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") == "ok":
            reading = _parse_aqicn_feed(data["data"], config, city_key, "geo")
            if reading:
                return [reading]
        else:
            logger.warning(f"AQICN (geo) non-ok status for {city_key}: {data.get('data')}")

    except httpx.HTTPStatusError as e:
        logger.error(f"AQICN (geo) HTTP error for {city_key}: {e.response.status_code}")
    except Exception as e:
        logger.error(f"AQICN (geo) error for {city_key}: {e}")

    # --- Attempt 2: keyword search fallback ---
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            search_resp = await client.get(
                "https://api.waqi.info/search/",
                params={"token": api_token, "keyword": config["name"]},
            )
            search_resp.raise_for_status()
            search_data = search_resp.json()

        if search_data.get("status") != "ok" or not search_data.get("data"):
            logger.info(f"AQICN (keyword): no search results for {city_key}")
            return []

        # Try candidates in order until one passes the distance check —
        # same reasoning as OpenAQ's multi-candidate loop: don't just
        # trust the first result blind.
        async with httpx.AsyncClient(timeout=30.0) as client:
            for candidate in search_data["data"][:5]:
                station_url = candidate.get("station", {}).get("url")
                if not station_url:
                    continue

                feed_resp = await client.get(
                    f"https://api.waqi.info/feed/{station_url}/",
                    params={"token": api_token},
                )
                if feed_resp.status_code != 200:
                    continue
                feed_data = feed_resp.json()
                if feed_data.get("status") != "ok":
                    continue

                reading = _parse_aqicn_feed(feed_data["data"], config, city_key, "keyword")
                if reading:
                    return [reading]

        logger.info(f"AQICN (keyword): no candidate for {city_key} passed the distance check")

    except httpx.HTTPStatusError as e:
        logger.error(f"AQICN (keyword) HTTP error for {city_key}: {e.response.status_code}")
    except Exception as e:
        logger.error(f"AQICN (keyword) error for {city_key}: {e}")

    return []




# =============================================================================
# AQICN MAP/BOUNDS — station discovery within a city
#
# New feature: instead of only ever seeing ONE AQI number per city, this
# discovers every real monitoring station AQICN has within the city's
# bounding box (confirmed real, documented endpoint: /map/bounds/), then
# fetches each individual station's own reading. This is what powers the
# "explore every station in this city" dropdown, distinct from the main
# Reading pipeline used for the ML models.
# =============================================================================
async def fetch_city_stations(city_key: str, api_token: Optional[str]) -> List[Dict[str, Any]]:
    config = CITY_CONFIGS.get(city_key)
    if not config or not api_token or "bbox" not in config:
        return []

    south, west, north, east = config["bbox"]
    readings: List[Dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            bounds_resp = await client.get(
                "https://api.waqi.info/map/bounds/",
                params={"latlng": f"{south},{west},{north},{east}", "token": api_token},
            )
            bounds_resp.raise_for_status()
            bounds_data = bounds_resp.json()

        if bounds_data.get("status") != "ok":
            logger.warning(f"AQICN map/bounds non-ok status for {city_key}: {bounds_data}")
            return []

        stations = bounds_data.get("data", [])
        if not stations:
            logger.info(f"AQICN map/bounds: no stations found for {city_key}")
            return []

        logger.info(f"AQICN map/bounds: found {len(stations)} stations for {city_key}")

        # Fetch every station's full reading concurrently — sequential
        # would make a 40-station city take way too long per poll cycle.
        async def _fetch_one(station: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            uid = station.get("uid")
            if uid is None:
                return None
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    feed_resp = await client.get(
                        f"https://api.waqi.info/feed/@{uid}/",
                        params={"token": api_token},
                    )
                if feed_resp.status_code != 200:
                    return None
                feed_data = feed_resp.json()
                if feed_data.get("status") != "ok":
                    return None

                feed = feed_data["data"]
                station_info = feed.get("city", {})
                iaqi = feed.get("iaqi", {})
                time_iso = (feed.get("time") or {}).get("iso")
                recorded_at = (
                    datetime.fromisoformat(time_iso) if time_iso else datetime.now(timezone.utc)
                )

                def val(key: str) -> Optional[float]:
                    item = iaqi.get(key)
                    return float(item["v"]) if item and item.get("v") is not None else None

                geo = station_info.get("geo") or [station.get("lat"), station.get("lon")]

                return {
                    "city": city_key,
                    "station_uid": str(uid),
                    "station_name": station_info.get("name", "Unknown station"),
                    "latitude": geo[0] if geo else station.get("lat"),
                    "longitude": geo[1] if geo else station.get("lon"),
                    "aqi_value": feed.get("aqi") if isinstance(feed.get("aqi"), int) else None,
                    "pm25": val("pm25"),
                    "pm10": val("pm10"),
                    "no2": val("no2"),
                    "so2": val("so2"),
                    "co": val("co"),
                    "o3": val("o3"),
                    "recorded_at": recorded_at,
                }
            except Exception as e:
                logger.error(f"AQICN station @{uid} fetch error for {city_key}: {e}")
                return None

        station_results = await asyncio.gather(*(_fetch_one(s) for s in stations))
        readings = [r for r in station_results if r is not None]

        logger.info(
            f"AQICN map/bounds: successfully fetched {len(readings)}/{len(stations)} "
            f"station readings for {city_key}"
        )

    except httpx.HTTPStatusError as e:
        logger.error(f"AQICN map/bounds HTTP error for {city_key}: {e.response.status_code}")
    except Exception as e:
        logger.error(f"AQICN map/bounds error for {city_key}: {e}")

    return readings


# =============================================================================
# OPENAQ MULTI-STATION DISCOVERY — proven to work for all 6 cities (AQICN's
# map/bounds turned out to only return real data for Delhi on this token —
# confirmed by directly testing the endpoint in a browser, not just our
# code). OpenAQ's /v3/locations search already returns multiple nearby
# stations in one call; we were only ever using the single best one. This
# reuses that same proven-working call, but keeps every candidate instead
# of discarding all but one — and gives genuine raw µg/m³ concentrations,
# not AQI sub-indices, which is a real improvement over the AQICN version.
# =============================================================================
async def fetch_city_stations_openaq(city_key: str, api_key: Optional[str]) -> List[Dict[str, Any]]:
    config = CITY_CONFIGS.get(city_key)
    if not config or not api_key:
        return []

    headers = {"X-API-Key": api_key}
    staleness_cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    readings: List[Dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            await _openaq_rate_limiter.wait()
            loc_resp = await client.get(
                "https://api.openaq.org/v3/locations",
                params={
                    "coordinates": f"{config['latitude']},{config['longitude']}",
                    "radius": 25000,
                    # Capped at 15, not 50 — OpenAQ's 60/min limit is
                    # shared across all 6 cities in the same cycle, so
                    # requesting 50 candidates per city was structurally
                    # guaranteed to trigger 429s no matter how well
                    # throttled the individual calls were.
                    "limit": 15,
                },
            )
            loc_resp.raise_for_status()
            locations = loc_resp.json().get("results", [])

            if not locations:
                logger.info(f"OpenAQ stations: no locations found for {city_key}")
                return []

            async def _fetch_one(location: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                location_id = location["id"]

                last_reported_str = (location.get("datetimeLast") or {}).get("utc")
                if last_reported_str:
                    last_reported_dt = datetime.fromisoformat(
                        last_reported_str.replace("Z", "+00:00")
                    )
                    if last_reported_dt < staleness_cutoff:
                        return None  # too old to bother showing in a "browse" list

                sensor_param_map = {
                    s["id"]: (s.get("parameter") or {}).get("name", "").lower()
                    for s in location.get("sensors", [])
                }

                try:
                    await _openaq_rate_limiter.wait()
                    latest_resp = await client.get(
                        f"https://api.openaq.org/v3/locations/{location_id}/latest"
                    )
                except Exception:
                    return None
                if latest_resp.status_code != 200:
                    return None
                results = latest_resp.json().get("results", [])
                if not results:
                    return None

                pm25 = pm10 = no2 = so2 = co = o3 = None
                most_recent = None
                for entry in results:
                    param_name = sensor_param_map.get(entry.get("sensorsId"), "")
                    value = entry.get("value")
                    dt_str = (entry.get("datetime") or {}).get("utc")
                    if dt_str:
                        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                        if most_recent is None or dt > most_recent:
                            most_recent = dt
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

                if all(v is None for v in (pm25, pm10, no2, so2, co, o3)):
                    return None

                coords = location.get("coordinates") or {}

                return {
                    "city": city_key,
                    "station_uid": f"openaq_{location_id}",
                    "station_name": location.get("name") or location.get("locality") or "Unknown station",
                    "latitude": coords.get("latitude", config["latitude"]),
                    "longitude": coords.get("longitude", config["longitude"]),
                    "aqi_value": calculate_aqi_from_pm25(pm25) if pm25 is not None else None,
                    "pm25": pm25,
                    "pm10": pm10,
                    "no2": no2,
                    "so2": so2,
                    "co": co,
                    "o3": o3,
                    "recorded_at": most_recent or datetime.now(timezone.utc),
                }

            station_results = await asyncio.gather(*(_fetch_one(loc) for loc in locations))
            readings = [r for r in station_results if r is not None]

            logger.info(
                f"OpenAQ stations: found {len(readings)} usable station "
                f"readings among {len(locations)} candidates for {city_key}"
            )

    except httpx.HTTPStatusError as e:
        logger.error(f"OpenAQ stations HTTP error for {city_key}: {e.response.status_code}")
    except Exception as e:
        logger.error(f"OpenAQ stations error for {city_key}: {e}")

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
        temp = main.get("temp")
        humidity = main.get("humidity")

        readings.append({
            "city": city_key,
            "latitude": config["latitude"],
            "longitude": config["longitude"],
            "reading_type": "weather",
            "source": "openweathermap",
            "recorded_at": datetime.fromtimestamp(data["dt"], tz=timezone.utc),
            "weather_temp": temp,
            "weather_humidity": humidity,
            "weather_wind_speed": wind.get("speed"),
            "weather_wind_direction": wind.get("deg"),
            "weather_pressure": main.get("pressure"),
            "weather_precipitation": data.get("rain", {}).get("1h", 0) or data.get("snow", {}).get("1h", 0) or None,
            "weather_visibility": visibility / 1000 if visibility else None,
            "weather_cloud_cover": clouds.get("all"),
            "weather_apparent_temp": main.get("feels_like"),
            # OpenWeatherMap's free tier doesn't return dew point or gusts
            # directly — Open-Meteo (below) covers those instead.
            "weather_dew_point": None,
            "weather_wind_gusts": wind.get("gust"),
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
                       "wind_direction_10m,surface_pressure,precipitation,cloud_cover,"
                       "dew_point_2m,apparent_temperature,wind_gusts_10m",
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
            "weather_dew_point": current.get("dew_point_2m"),
            "weather_apparent_temp": current.get("apparent_temperature"),
            "weather_wind_gusts": current.get("wind_gusts_10m"),
        })

    except httpx.HTTPStatusError as e:
        logger.error(f"Open-Meteo HTTP error for {city_key}: {e.response.status_code}")
    except Exception as e:
        logger.error(f"Open-Meteo error for {city_key}: {e}")

    return readings


# =============================================================================
# OPEN-METEO FLOOD API — river discharge, no key required
#
# Added after Phase B shipped: AQI and weather are both "properties of a
# place" that exist continuously, so polling them from day one builds
# useful rolling history. River discharge is the same kind of thing —
# it's always there, always changing gradually — so it belongs in the
# same continuous polling loop, not deferred to whenever the flood model
# actually gets built in Phase F. (Cyclone tracking, by contrast,
# genuinely is fine to defer — there's usually no active storm to track
# at all, so polling it constantly has little value until the model
# that uses it actually exists.)
# =============================================================================
async def fetch_openmeteo_flood(city_key: str) -> List[Dict[str, Any]]:
    config = CITY_CONFIGS.get(city_key)
    if not config:
        return []

    readings: List[Dict[str, Any]] = []

    try:
        params = {
            "latitude": config["latitude"],
            "longitude": config["longitude"],
            "daily": "river_discharge,river_discharge_mean,river_discharge_median,"
                     "river_discharge_max,river_discharge_min",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get("https://flood-api.open-meteo.com/v1/flood", params=params)
            resp.raise_for_status()
            data = resp.json()

        daily = data.get("daily", {})
        times = daily.get("time", [])

        if not times:
            return []

        # Today's value only — historical/future values matter for
        # training (Phase F), not for this live polling loop.
        recorded_at = datetime.fromisoformat(times[0]).replace(tzinfo=timezone.utc)
        discharge = daily.get("river_discharge", [None])[0]

        if discharge is not None:
            readings.append({
                "city": city_key,
                "latitude": config["latitude"],
                "longitude": config["longitude"],
                "reading_type": "flood",
                "source": "openmeteo_flood",
                "recorded_at": recorded_at,
                "flood_river_discharge": discharge,
                "flood_river_discharge_mean": daily.get("river_discharge_mean", [None])[0],
                "flood_river_discharge_median": daily.get("river_discharge_median", [None])[0],
                "flood_river_discharge_max": daily.get("river_discharge_max", [None])[0],
                "flood_river_discharge_min": daily.get("river_discharge_min", [None])[0],
            })

    except httpx.HTTPStatusError as e:
        logger.error(f"Open-Meteo Flood HTTP error for {city_key}: {e.response.status_code}")
    except Exception as e:
        logger.error(f"Open-Meteo Flood error for {city_key}: {e}")

    return readings


# =============================================================================
# GDACS — active tropical cyclone tracking (global, not per-city)
#
# Opportunistic early addition, agreed as a cheap "just in case" while
# full integration waits for Phase E. GDACS's public field names for
# severity/wind speed aren't fully pinned down from their docs alone —
# this logs the raw properties block on every call so we can refine
# parsing once we've seen real responses, the same honest approach that
# caught the AQICN station-collision bug earlier.
# =============================================================================
GDACS_EVENT_LIST_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"

# Roughly the North Indian Ocean basin — the storms actually relevant to
# India. Loosen or remove this if you want global storm tracking instead.
NORTH_INDIAN_OCEAN_BBOX = {"lat_min": 0, "lat_max": 30, "lon_min": 50, "lon_max": 100}


async def fetch_gdacs_cyclones() -> List[Dict[str, Any]]:
    observations: List[Dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(GDACS_EVENT_LIST_URL)
            resp.raise_for_status()
            data = resp.json()

        features = data.get("features", []) if isinstance(data, dict) else []
        if not features:
            logger.info("GDACS: no events returned this cycle")
            return []

        for feature in features:
            props = feature.get("properties", {})
            event_type = props.get("eventtype") or props.get("eventType")

            if event_type != "TC":
                continue

            # GDACS's event list includes historical/archived storms
            # alongside genuinely active ones — its own "iscurrent" flag
            # is what actually distinguishes them. Without this check we
            # were re-storing long-over 2025 storms as if they were live,
            # every single polling cycle, forever.
            if str(props.get("iscurrent", "false")).lower() != "true":
                continue

            geometry = feature.get("geometry", {})
            coords = geometry.get("coordinates", [None, None])
            lon, lat = (coords[0], coords[1]) if len(coords) >= 2 else (None, None)

            if lat is not None and not (
                NORTH_INDIAN_OCEAN_BBOX["lat_min"] <= lat <= NORTH_INDIAN_OCEAN_BBOX["lat_max"]
                and NORTH_INDIAN_OCEAN_BBOX["lon_min"] <= lon <= NORTH_INDIAN_OCEAN_BBOX["lon_max"]
            ):
                continue  # storm exists, but not in our region of interest

            # Log the raw block once per storm per cycle — this is what
            # lets us confirm/refine the field names below against real
            # data rather than guessing blind.
            logger.info(f"GDACS raw TC properties: {props}")

            severity = props.get("severitydata", {}) or {}

            observations.append({
                "storm_id": str(props.get("eventid", "unknown")),
                "storm_name": props.get("eventname") or props.get("name"),
                "latitude": lat,
                "longitude": lon,
                "alert_level": props.get("alertlevel"),
                # These two are genuinely uncertain until we see a real
                # active storm's raw properties (logged above) — GDACS
                # sometimes reports this as descriptive text rather than
                # a clean number, hence the fallback to raw text storage.
                "wind_speed_kmh": severity.get("severity") if isinstance(severity.get("severity"), (int, float)) else None,
                "category": severity.get("severitytext"),
                "raw_severity_text": str(severity) if severity else None,
                "recorded_at": datetime.now(timezone.utc),
            })

    except httpx.HTTPStatusError as e:
        logger.error(f"GDACS HTTP error: {e.response.status_code}")
    except Exception as e:
        logger.error(f"GDACS error: {e}")

    return observations


# =============================================================================
# AGGREGATE FETCHERS
# =============================================================================
def _merge_aqi_readings(
    openaq_reading: Optional[Dict[str, Any]],
    aqicn_reading: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Previous version of this function combined AQICN's composite AQI with
    OpenAQ's pollutant breakdown into one row — but those two numbers can
    come from two different physical stations in different parts of the
    city, so the result could be genuinely self-contradictory (e.g. a
    "Satisfactory" headline AQI sitting next to a PM2.5 reading that would
    imply "Very Unhealthy" on its own). That's not just imprecise, it's
    actively misleading for a disaster-alert product.

    Correct approach: never mix two stations into one reading. Pick ONE
    internally-consistent source per city per cycle. OpenAQ is preferred
    when it has usable data, since it gives real concentrations AND a
    composite AQI we compute ourselves from that same station's own
    PM2.5 — headline number and breakdown always agree, because they
    come from the same report. AQICN (composite only, no breakdown,
    per the earlier unit-mismatch fix) is the fallback when OpenAQ has
    nothing usable that cycle.
    """
    if openaq_reading:
        return [openaq_reading]
    if aqicn_reading:
        return [aqicn_reading]
    return []


async def fetch_all_for_city(
    city_key: str,
    openaq_key: Optional[str] = None,
    aqicn_token: Optional[str] = None,
    openweathermap_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    aqi_tasks = [
        fetch_openaq(city_key, openaq_key),
        fetch_aqicn(city_key, aqicn_token),
    ]
    other_tasks = [
        fetch_openmeteo(city_key),
        fetch_openweathermap(city_key, openweathermap_key),
        fetch_openmeteo_flood(city_key),
    ]

    aqi_results = await asyncio.gather(*aqi_tasks, return_exceptions=True)
    other_results = await asyncio.gather(*other_tasks, return_exceptions=True)

    openaq_reading = None
    aqicn_reading = None
    for label, result in zip(("openaq", "aqicn"), aqi_results):
        if isinstance(result, list) and result:
            if label == "openaq":
                openaq_reading = result[0]
            else:
                aqicn_reading = result[0]
        elif isinstance(result, Exception):
            logger.error(f"{label} fetch task failed for {city_key}: {result}")

    all_readings: List[Dict[str, Any]] = _merge_aqi_readings(openaq_reading, aqicn_reading)

    for result in other_results:
        if isinstance(result, list):
            all_readings.extend(result)
        elif isinstance(result, Exception):
            logger.error(f"Fetch task failed for {city_key}: {result}")

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
    whatever came back, check for active cyclones, discover per-station
    readings within each city, wait, repeat. Started from main.py's
    lifespan handler on app startup.
    """
    # Imported here, not at module top, to avoid a circular import
    # between ingestion.py and database.py/models at startup.
    from app.database import SessionLocal
    from app.models.readings import Reading
    from app.models.cyclone_observations import CycloneObservation
    from app.models.station_readings import CityStationReading
    from app.config import get_settings

    settings = get_settings()
    cycle_count = 0

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

            # Opportunistic cyclone check — cheap, global, not per-city.
            cyclone_data = await fetch_gdacs_cyclones()
            if cyclone_data:
                db = SessionLocal()
                try:
                    for cd in cyclone_data:
                        db.add(CycloneObservation(**cd))
                    db.commit()
                    logger.info(f"Stored {len(cyclone_data)} cyclone observation(s)")
                except Exception as e:
                    db.rollback()
                    logger.error(f"DB error storing cyclone observations: {e}")
                finally:
                    db.close()

            # Station discovery — every real cycle would stack this
            # volume of calls on top of the main pipeline's own OpenAQ
            # usage, both drawing from the same 60/min quota. Station
            # lists also don't meaningfully change minute to minute, so
            # running this every 3rd cycle (~15 min) instead of every
            # cycle (5 min) cuts its contribution to the shared rate
            # limit by 3x for free, at no real cost to freshness.
            if cycle_count % 3 == 0:
                # AQICN's map/bounds only returns real data for Delhi on
                # this token (confirmed by testing the endpoint directly,
                # not just our code — other cities give a genuinely empty
                # result, not an error). OpenAQ's locations search is
                # proven to work for all 6 cities, so it runs too,
                # covering the cities AQICN can't.
                aqicn_station_results = await asyncio.gather(
                    *(
                        fetch_city_stations(city_key, settings.aqicn_api_token)
                        for city_key in CITY_CONFIGS
                    ),
                    return_exceptions=True,
                )
                openaq_station_results = await asyncio.gather(
                    *(
                        fetch_city_stations_openaq(city_key, settings.openaq_api_key)
                        for city_key in CITY_CONFIGS
                    ),
                    return_exceptions=True,
                )
                station_results = aqicn_station_results + openaq_station_results

                all_station_readings: List[Dict[str, Any]] = []
                for result in station_results:
                    if isinstance(result, list):
                        all_station_readings.extend(result)
                    elif isinstance(result, Exception):
                        logger.error(f"Station discovery failed: {result}")

                if all_station_readings:
                    db = SessionLocal()
                    try:
                        stored = 0
                        updated = 0
                        for sd in all_station_readings:
                            existing = (
                                db.query(CityStationReading)
                                .filter(
                                    CityStationReading.city == sd["city"],
                                    CityStationReading.station_uid == sd["station_uid"],
                                )
                                .first()
                            )
                            if existing:
                                for key, value in sd.items():
                                    setattr(existing, key, value)
                                updated += 1
                            else:
                                db.add(CityStationReading(**sd))
                                stored += 1
                        db.commit()
                        logger.info(
                            f"Station readings: {stored} new, {updated} updated"
                        )
                    except Exception as e:
                        db.rollback()
                        logger.error(f"DB error storing station readings: {e}")
                    finally:
                        db.close()
            else:
                logger.info(
                    f"Skipping station discovery this cycle "
                    f"(runs every 3rd cycle, currently {cycle_count % 3} of 3 until next run)"
                )

            cycle_count += 1

        except Exception as e:
            logger.error(f"Polling cycle error: {e}")

        await asyncio.sleep(POLLING_INTERVAL_SECONDS)