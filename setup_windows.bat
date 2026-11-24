@echo off
REM ============================================================================
REM  One-time setup: project-local .venv, pinned backend dependencies, frontend deps.
REM  Run from anywhere; paths are relative to this file.
REM ============================================================================
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"

echo [1/4] Looking for Python 3.14 (falls back to 3.12 if 3.14 is not installed)...
set "PYCMD="
py -3.14 --version >nul 2>&1 && set "PYCMD=py -3.14"
if not defined PYCMD ( py -3.12 --version >nul 2>&1 && set "PYCMD=py -3.12" )
if not defined PYCMD (
  echo ERROR: Neither Python 3.14 nor 3.12 was found via the "py" launcher. Install 64-bit Python from python.org and tick "py launcher".
  exit /b 1
)
echo Using: %PYCMD%

if not exist ".venv\Scripts\python.exe" (
  echo [2/4] Creating .venv ...
  %PYCMD% -m venv .venv || ( echo ERROR: venv creation failed & exit /b 1 )
) else (
  echo [2/4] .venv already exists - reusing it.
)

echo [3/4] Installing PINNED backend requirements (do not change scikit-learn/xgboost versions)...
".venv\Scripts\python.exe" -m pip install --upgrade pip || exit /b 1
".venv\Scripts\python.exe" -m pip install -r "backend\requirements.txt" || ( echo ERROR: pip install failed & exit /b 1 )

if not exist "backend\.env" ( copy /y ".env.example" "backend\.env" >nul & echo Created backend\.env from .env.example - add optional keys there. )
if not exist "frontend\.env" ( copy /y "frontend\.env.example" "frontend\.env" >nul & echo Created frontend\.env )

echo [4/4] Installing frontend dependencies (needs Node.js 18+ / npm)...
where npm >nul 2>&1 || ( echo ERROR: npm not found. Install Node.js LTS from nodejs.org & exit /b 1 )
pushd frontend
call npm install --no-audit --no-fund || ( popd & echo ERROR: npm install failed & exit /b 1 )
popd

echo.
echo Setup complete. Next: start_project.bat
endlocal
