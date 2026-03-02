@echo off
setlocal enabledelayedexpansion

set "ROOT=%~dp0"
set "VENV_PY="
set "BOOTSTRAP_PY=python"

echo [Taverna] Root: %ROOT%

if exist "C:\Users\Fuchs\AppData\Local\Programs\Python\Python311\python.exe" (
  set "BOOTSTRAP_PY=C:\Users\Fuchs\AppData\Local\Programs\Python\Python311\python.exe"
)

if exist "%ROOT%backend\.venv\bin\python.exe" if not exist "%ROOT%backend\.venv\Scripts\python.exe" (
  echo [Backend] Recreating virtual environment with Windows Python...
  rmdir /s /q "%ROOT%backend\.venv"
)

if not exist "%ROOT%backend\.venv\Scripts\python.exe" if not exist "%ROOT%backend\.venv\bin\python.exe" (
  echo [Backend] Creating virtual environment...
  "%BOOTSTRAP_PY%" -m venv "%ROOT%backend\.venv"
  if errorlevel 1 goto :error
)

if exist "%ROOT%backend\.venv\Scripts\python.exe" (
  set "VENV_PY=%ROOT%backend\.venv\Scripts\python.exe"
) else if exist "%ROOT%backend\.venv\bin\python.exe" (
  set "VENV_PY=%ROOT%backend\.venv\bin\python.exe"
) else (
  echo [Backend] Cannot find venv python executable.
  goto :error
)

if not exist "%ROOT%frontend\node_modules" (
  echo [Frontend] Installing npm dependencies...
  pushd "%ROOT%frontend"
  call npm --script-shell="C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" install
  if errorlevel 1 (
    popd
    goto :error
  )
  popd
)

echo [Infra] Starting PostgreSQL (docker compose)...
pushd "%ROOT%"
docker compose up -d postgres
if errorlevel 1 (
  popd
  goto :error
)
popd

echo [Backend] Installing Python dependencies...
pushd "%ROOT%backend"
"%VENV_PY%" -m pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt
if errorlevel 1 (
  popd
  goto :error
)

echo [Backend] Running database migrations...
"%VENV_PY%" -m alembic upgrade head
if errorlevel 1 (
  popd
  goto :error
)

echo [Backend] Seeding default user...
"%VENV_PY%" scripts\seed_default_user.py
if errorlevel 1 (
  popd
  goto :error
)
popd

echo [Backend] Starting on http://localhost:8000 ...
start "Taverna Backend" powershell -NoExit -Command "Set-Location -LiteralPath '%ROOT%backend'; if (Test-Path '.venv\\Scripts\\python.exe') { $py='.venv\\Scripts\\python.exe' } else { $py='.venv\\bin\\python.exe' }; & $py -m uvicorn main:app --reload --host 0.0.0.0 --port 8000"

echo [Frontend] Starting on http://localhost:5173 ...
start "Taverna Frontend" powershell -NoExit -Command "Set-Location -LiteralPath '%ROOT%frontend'; $env:npm_config_script_shell='C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe'; npm run dev"

echo [Taverna] Both services are launching in separate windows.
echo [Taverna] Press any key to close this window...
pause >nul
exit /b 0

:error
echo [Taverna] Startup failed. Check logs above.
echo [Taverna] Press any key to close this window...
pause >nul
exit /b 1


