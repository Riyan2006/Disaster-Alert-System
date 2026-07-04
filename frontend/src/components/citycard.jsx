import { useState } from "react";

function getAqiCategory(aqi) {
  if (aqi === null || aqi === undefined) return { label: "No data", color: "var(--text-faint)" };
  if (aqi <= 50) return { label: "Good", color: "var(--green)" };
  if (aqi <= 100) return { label: "Satisfactory", color: "#A8D86E" };
  if (aqi <= 200) return { label: "Moderate", color: "var(--amber)" };
  if (aqi <= 300) return { label: "Poor", color: "var(--red)" };
  return { label: "Severe", color: "#991B1B" };
}

export default function CityCard({ city }) {
  const [expanded, setExpanded] = useState(false);
  const aqi = city.aqi;
  const weather = city.weather;
  const category = getAqiCategory(aqi?.value);

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
        </div>
      )}
    </div>
  );
}