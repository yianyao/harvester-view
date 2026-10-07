@echo off
rem ============================================================
rem  harvester-view starter for machines with a SYSTEM Python
rem  (>= 3.10 on PATH, or via the 'py' launcher).
rem  Unlike start.cmd (hardcoded WorkBuddy bundled interpreter),
rem  this one auto-detects: 'py -3' first, then 'python'.
rem  Keep this file PURE ASCII: cmd.exe parses batch files with
rem  the ANSI/OEM codepage, NOT UTF-8. Keep echo lines free of
rem  double quotes: a caret inside a quoted region is not an
rem  escape and would leak literally into the output.
rem ============================================================
setlocal
set "SRV=%~dp0..\session-harvester"

rem --- find a python >= 3.10 ---
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo [start] no Python ^>= 3.10 found. Tried 'py -3' and 'python'.
  echo         Install Python 3.10+ from python.org ^(tick Add to
  echo         PATH^), or use start.cmd instead ^(bundled interpreter^).
  pause
  exit /b 1
)

if not exist "%SRV%\harvester.db" (
  echo [start] database not found: %SRV%\harvester.db
  pause
  exit /b 1
)
if not exist "%SRV%\cards_pending" mkdir "%SRV%\cards_pending"

rem %PY% may contain a space ('py -3'), so it stays UNQUOTED below;
rem quoted it would be looked up as a single command named 'py -3'.
start "harvester api-serve :8765" /D "%SRV%" %PY% -m harvester api-serve --db "%SRV%\harvester.db" --cards-root "%SRV%\cards_pending"
start "harvester-view :8088" /D "%~dp0" %PY% view.py
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8088/
echo [start] api-serve + view launched with: %PY%
echo [start] Close both console windows to stop.
