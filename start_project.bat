@echo off
REM Starts backend + frontend in two windows, waits until the API answers, then opens the browser.
setlocal
set "ROOT=%~dp0"
start "Traffic Bottleneck - Backend (FastAPI)" cmd /k ""%ROOT%start_backend.bat""
start "Traffic Bottleneck - Frontend (React)" cmd /k ""%ROOT%start_frontend.bat""

echo Waiting for the backend to answer on http://127.0.0.1:8000/health ...
powershell -NoProfile -Command "$ok=$false; for($i=0;$i -lt 90;$i++){ try { $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2; if($r.StatusCode -eq 200){$ok=$true;break} } catch {}; Start-Sleep -Seconds 2 }; if($ok){exit 0}else{exit 1}"
if errorlevel 1 ( echo Backend did not become ready in time - check the backend window. ) else ( echo Backend is up. )

timeout /t 4 /nobreak >nul
start "" "http://127.0.0.1:5173"
echo Frontend: http://127.0.0.1:5173   API docs: http://127.0.0.1:8000/docs
endlocal
