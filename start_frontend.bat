@echo off
REM Starts the React (Vite) dev server on http://127.0.0.1:5173
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%frontend"
if not exist ".env" copy /y ".env.example" ".env" >nul
where npm >nul 2>&1 || ( echo ERROR: npm not found. Install Node.js LTS from nodejs.org & exit /b 1 )
if not exist "node_modules" ( echo Installing frontend dependencies... & call npm install --no-audit --no-fund || exit /b 1 )
echo Starting React on http://127.0.0.1:5173
call npm run dev
endlocal
