@echo off
rem ============================================================
rem  harvester-view starter for machines with a SYSTEM Python
rem  Detection order: 'py -3' -> 'python' -> 'uv python find'
rem  (uv-managed interpreters are NOT on PATH; uv resolves them)
rem  Keep this file PURE ASCII + CRLF: cmd.exe parses batch files
rem  with the ANSI/OEM codepage, NOT UTF-8. Echo lines must avoid
rem  double quotes: a caret inside a quoted region is not an
rem  escape and leaks literally into the output.
rem ============================================================
setlocal
set "SRV=%~dp0..\session-harvester"

rem --- find a python >= 3.10: py launcher, then python, then uv-managed ---
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  for /f "usebackq delims=" %%i in (`uv python find 2^>nul`) do set "PY=%%i"
)
rem version re-check (uv path may point to a managed interpreter)
if defined PY (
  "%PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
  if errorlevel 1 set "PY="
)

if not defined PY (
  echo [start] no Python ^>= 3.10 found. Tried 'py -3', 'python' and 'uv'.
  echo         Install Python 3.10+ ^(tick Add to PATH^), or install uv
  echo         ^(https://docs.astral.sh/uv/^) and run: uv python install 3.12
  pause
  exit /b 1
)

if not exist "%SRV%\harvester.db" (
  echo [start] database not found: %SRV%\harvester.db
  pause
  exit /b 1
)
if not exist "%SRV%\cards_pending" mkdir "%SRV%\cards_pending"

rem %PY% may contain a space ('py -3'), so it stays UNQUOTED below.
start "harvester api-serve :8765" /D "%SRV%" %PY% -m harvester api-serve --db "%SRV%\harvester.db" --cards-root "%SRV%\cards_pending"
start "harvester-view :8088" /D "%~dp0" %PY% view.py
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8088/
echo [start] api-serve + view launched with: %PY%
echo [start] Close both console windows to stop.
