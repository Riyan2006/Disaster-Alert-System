import { useEffect, useState } from "react";
import { pingBackend } from "./services/api";
import "./styles/theme.css";

/*
  Setu — root app component.

  Phase A only needs this to prove one thing: the frontend can reach
  the backend and render something real from it. Everything else
  (Dashboard, routing between pages, real components) gets built
  starting in Phase B — this file gets replaced/expanded then.
*/
function App() {
  const [backendStatus, setBackendStatus] = useState("checking...");
  const [error, setError] = useState(null);

  useEffect(() => {
    pingBackend()
      .then((data) => setBackendStatus(data.status))
      .catch((err) => setError(err.message));
  }, []);

  return (
    <div style={{ padding: "48px", fontFamily: "var(--sans)" }}>
      <h1 style={{ fontFamily: "var(--mono)", fontSize: "20px", marginBottom: "8px" }}>
        SETU
      </h1>
      <p style={{ color: "var(--text-dim)", marginBottom: "24px" }}>
        Phase A skeleton — frontend ↔ backend connectivity check.
      </p>

      <div
        style={{
          background: "var(--bg-card)",
          border: "1px solid var(--line)",
          borderRadius: "10px",
          padding: "16px 20px",
          fontFamily: "var(--mono)",
          fontSize: "14px",
        }}
      >
        Backend status:{" "}
        {error ? (
          <span style={{ color: "var(--red)" }}>error — {error}</span>
        ) : (
          <span style={{ color: "var(--green)" }}>{backendStatus}</span>
        )}
      </div>
    </div>
  );
}

export default App;
