@echo off
REM One-time download+cache of the real Bhubaneswar OpenStreetMap road network (needs internet).
setlocal
set "ROOT=%~dp0..\"
cd /d "%ROOT%backend"
"%ROOT%.venv\Scripts\python.exe" scripts\download_osm.py %*
endlocal
