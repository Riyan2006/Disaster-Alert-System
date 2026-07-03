# Setu

A disaster alert system that pre-stages predictions on your phone while the
network still works, fires them locally with no connection needed, and
relays new hazard reports peer-to-peer when the network is down.

Built for SmartAIthon 2026. Full architecture and build methodology live in
`docs/` — read those before touching code.

## Repo layout

- `backend/` — Python, FastAPI, the three trained ML models (AQI, Cyclone, Flood)
- `frontend/` — React + Vite, the installable PWA
- `docs/` — the build plan and the phase-by-phase blueprint this project follows

## Current status

Phase A (foundation skeleton) — backend and frontend both boot locally and
can talk to each other. Nothing beyond that is built yet.

## Local development

**Backend:**
```
cd backend
cp .env.example .env   # fill in real values as later phases need them
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Runs at `http://127.0.0.1:8000`.

**Frontend:**
```
cd frontend
npm install
npm run dev
```
Runs at `http://localhost:5173`.

## Build plan

See `docs/setu-build-plan-v2.html` for the architecture and reasoning, and
`docs/setu-blueprint.html` for the exact phase-by-phase steps. Work through
phases in order — each one's "definition of done" should be checked off
before starting the next.
