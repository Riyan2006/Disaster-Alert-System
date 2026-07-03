/*
  Setu — API service.

  Every call the frontend makes to the backend goes through here,
  rather than scattering fetch() calls through components. Later
  phases add more functions to this file (getPredictions, getReadings,
  submitReport, etc.) — Phase A just needs one, to prove the wiring works.
*/

// In local dev this points at the backend running on your machine.
// Once the backend is deployed (Phase A step 2), this becomes the
// real Render URL, read from an environment variable instead of
// hardcoded — see frontend/.env.example, added at that point.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

export async function pingBackend() {
  const response = await fetch(`${API_BASE_URL}/`);
  if (!response.ok) {
    throw new Error(`Backend responded with status ${response.status}`);
  }
  return response.json();
}
