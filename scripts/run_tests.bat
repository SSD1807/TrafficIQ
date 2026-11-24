@echo off
REM Runs the backend test-suite and the frontend type-check/lint/build.
setlocal
set "ROOT=%~dp0..\"
cd /d "%ROOT%backend"
"%ROOT%.venv\Scripts\python.exe" -m pytest -q || exit /b 1
cd /d "%ROOT%frontend"
call npm run typecheck || exit /b 1
call npm run lint || exit /b 1
call npm test || exit /b 1
call npm run build || exit /b 1
echo All checks passed.
endlocal
