import { useState } from "react";
import { apiGet } from "../services/api";

function getAqiCategory(aqi) {
  if (aqi === null || aqi === undefined) return { label: "No data", color: "var(--text-faint)" };
  if (aqi <= 50) return { label: "Good", color: "var(--green)" };
  if (aqi <= 100) return { label: "Satisfactory", color: "#A8D86E" };
  if (aqi <= 200) return { label: "Moderate", color: "var(--amber)" };
  if (aqi <= 300) return { label: "Poor", color: "var(--red)" };
  return { label: "Severe", color: "#991B1B" };
}

function StationRow({ station }) {
  const category = getAqiCategory(station.aqi_value);
  return (
    <div
      style={{
        padding: "10px 0",
        borderTop: "1px solid var(--line-soft)",
        fontFamily: "var(--mono)",
        fontSize: "12px",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ color: "var(--text)" }}>{station.station_name}</span>
        <span style={{ color: category.color, fontWeight: 700 }}>
          {station.aqi_value != null ? station.aqi_value : "--"}
        </span>
      </div>
      <div style={{ color: "var(--text-faint)", marginTop: "3px" }}>
        PM2.5: {station.pm25 ?? "--"} · PM10: {station.pm10 ?? "--"} · NO2: {station.no2 ?? "--"}
        {" "}· SO2: {station.so2 ?? "--"} · O3: {station.o3 ?? "--"}
      </div>
    </div>
  );
}

export default function CityCard({ city }) {
  const [expanded, setExpanded] = useState(false);
  const [showStations, setShowStations] = useState(false);
  const [stations, setStations] = useState(null);
  const [loadingStations, setLoadingStations] = useState(false);
  const [stationsError, setStationsError] = useState(null);

  const aqi = city.aqi;
  const weather = city.weather;
  const category = getAqiCategory(aqi?.value);

  async function handleToggleStations(e) {
    e.stopPropagation(); // don't also collapse/expand the outer card
    const next = !showStations;
    setShowStations(next);

    // Lazy-load: only fetch the first time this is opened, not on every
    // toggle, and not for cities the user never expands.
    if (next && stations === null && !loadingStations) {
      setLoadingStations(true);
      setStationsError(null);
      try {
        const data = await apiGet(`/api/data/stations/${city.city}`);
        setStations(data.stations || []);
      } catch (err) {
        setStationsError(err.message);
      } finally {
        setLoadingStations(false);
      }
    }
  }

  return (
    <div
      onClick={() => setExpanded(!expanded)}
      style={{
        background: "var(--bg-card)",
        border: `1px solid ${expanded ? "var(--amber)" : "var(--line)"}`,
        borderRadius: "14px",
        padding: "20px",
        cursor: "pointer",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <h3 style={{ fontSize: "18px", fontWeight: 700 }}>{city.city_name}</h3>
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: "12px",
            padding: "3px 10px",
            borderRadius: "100px",
            background: category.color,
            color: "#111",
          }}
        >
          {aqi?.value != null ? `AQI ${aqi.value}` : "No data"}
        </span>
      </div>

      <div style={{ color: category.color, fontSize: "13px", fontWeight: 600, margin: "6px 0 14px" }}>
        {category.label}
      </div>

      <div style={{ display: "flex", gap: "18px", fontFamily: "var(--mono)", fontSize: "13px", color: "var(--text-dim)" }}>
        <span>{weather?.temp != null ? `${Math.round(weather.temp)}°C` : "--"}</span>
        <span>{weather?.humidity != null ? `${Math.round(weather.humidity)}% humidity` : "--"}</span>
        <span>{weather?.wind_speed != null ? `${weather.wind_speed.toFixed(1)} m/s wind` : "--"}</span>
      </div>

      {expanded && (
        <div style={{ marginTop: "16px", paddingTop: "16px", borderTop: "1px solid var(--line-soft)", fontFamily: "var(--mono)", fontSize: "12.5px", color: "var(--text-dim)" }}>
          <div>PM2.5: {aqi?.pm25 ?? "--"} · PM10: {aqi?.pm10 ?? "--"}</div>
          <div>NO2: {aqi?.no2 ?? "--"} · SO2: {aqi?.so2 ?? "--"} · O3: {aqi?.o3 ?? "--"}</div>
          <div style={{ marginTop: "8px", color: "var(--text-faint)" }}>
            Source: {aqi?.source ?? "--"} · Recorded: {aqi?.recorded_at ? new Date(aqi.recorded_at).toLocaleString() : "--"}
          </div>

          <button
            onClick={handleToggleStations}
            style={{
              marginTop: "14px",
              width: "100%",
              padding: "8px",
              background: "var(--bg-raised)",
              border: "1px solid var(--line)",
              borderRadius: "8px",
              color: "var(--text)",
              fontFamily: "var(--mono)",
              fontSize: "11.5px",
              cursor: "pointer",
            }}
          >
            {showStations ? "▲ Hide all stations" : "▼ View all stations in this city"}
          </button>

          {showStations && (
            <div style={{ marginTop: "8px" }}>
              {loadingStations && <div style={{ color: "var(--text-faint)", padding: "8px 0" }}>Loading stations…</div>}
              {stationsError && <div style={{ color: "var(--red)", padding: "8px 0" }}>Couldn't load stations: {stationsError}</div>}
              {stations && stations.length === 0 && (
                <div style={{ color: "var(--text-faint)", padding: "8px 0" }}>No stations found for this city yet.</div>
              )}
              {stations && stations.length > 0 && (
                <div style={{ maxHeight: "280px", overflowY: "auto" }}>
                  {stations.map((s) => (
                    <StationRow key={s.station_uid} station={s} />
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}