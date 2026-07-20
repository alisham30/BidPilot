@echo off
rem BidPilot — start everything (database, backend, dashboard)
cd /d "%~dp0"

echo Starting Postgres (Docker)...
docker compose up -d

echo Starting backend on http://localhost:8000 ...
start "BidPilot backend" cmd /k "cd /d "%~dp0backend" && .venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

echo Starting dashboard on http://localhost:5173 ...
start "BidPilot dashboard" cmd /k "cd /d "%~dp0frontend" && npx vite --port 5173"

echo.
echo BidPilot is starting — open http://localhost:5173
