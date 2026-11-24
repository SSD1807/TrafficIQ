@echo off
REM Starts the FastAPI backend on http://127.0.0.1:8000 using the project-local .venv.
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%.venv\Scripts\python.exe" (
  echo .venv not found - running setup_windows.bat first...
  call "%ROOT%setup_windows.bat" || exit /b 1
)
if not exist "%ROOT%backend\.env" copy /y "%ROOT%.env.example" "%ROOT%backend\.env" >nul

REM The backend resolves cache\, models\, dataset\ relative to ITS folder, so run from backend\.
cd /d "%ROOT%backend"

if not exist "cache\bhubaneswar_drive.graphml" (
  echo.
  echo NOTE: No cached OpenStreetMap road network found. Routing will use the 8-node SYNTHETIC FALLBACK graph.
  echo       For real Bhubaneswar roads run ONCE ^(needs internet, ~1-5 min^):  scripts\download_osm.bat
  echo.
)
echo Starting FastAPI on http://127.0.0.1:8000  ^(docs: http://127.0.0.1:8000/docs^)
"%ROOT%.venv\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
endlocal
