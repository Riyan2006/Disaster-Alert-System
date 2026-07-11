import { useEffect, useState } from "react";
import { apiGet } from "../services/api";
import CityCard from "../src/components/citycard";

export default function Dashboard() {
  const [cities, setCities] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);

  async function load() {
    try {
      const data = await apiGet("/api/data/current");
      setCities(data);
      setError(null);
      setLastUpdated(new Date());
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    const interval = setInterval(load, 5 * 60 * 1000); // refresh every 5 min
    return () => clearInterval(interval);
  }, []);

  return (
    <div style={{ maxWidth: "1100px", margin: "0 auto", padding: "32px 24px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "24px" }}>
        <div>
          <h1 style={{ fontFamily: "var(--mono)", fontSize: "22px" }}>SETU</h1>
          <p style={{ color: "var(--text-dim)", fontSize: "14px" }}>Live environmental monitor</p>
        </div>
        {lastUpdated && (
          <span style={{ fontFamily: "var(--mono)", fontSize: "11.5px", color: "var(--text-faint)" }}>
            Updated {lastUpdated.toLocaleTimeString()}
          </span>
        )}
      </div>

      {loading && <p style={{ color: "var(--text-dim)" }}>Loading live data…</p>}

      {error && (
        <p style={{ color: "var(--red)" }}>
          Couldn't load data: {error}
        </p>
      )}

      {!loading && !error && cities.length === 0 && (
        <p style={{ color: "var(--text-dim)" }}>
          No readings yet — the backend's first polling cycle may not have run.
          Give it a few minutes after deploy, or check the Render logs.
        </p>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: "16px" }}>
        {cities.map((city) => (
          <CityCard key={city.city} city={city} />
        ))}
      </div>
    </div>
  );
}