@echo off
rem ============================================================
rem  harvester-view one-click starter
rem  Starts BOTH: api-serve (upstream, port 8765) + view (8088),
rem  then opens the browser. Close the two console windows to stop.
rem
rem  NOTE 1: keep this file PURE ASCII. cmd.exe parses batch files
rem  with the ANSI/OEM codepage, NOT UTF-8 -- non-ASCII comments
rem  in a UTF-8 file break parsing and the script silently dies.
rem
rem  NOTE 2: the python path below is the WorkBuddy bundled
rem  interpreter. This machine has NO standalone Python on the
rem  persistent PATH (verified 2026-10-07). If that ever changes,
rem  update PY here.
rem ============================================================
set "PY=C:\Users\yianyao\.workbuddy\binaries\python\versions\3.13.12\python.exe"
set "SRV=%~dp0..\session-harvester"

if not exist "%PY%" (
  echo [start] python not found: %PY%
  pause
  exit /b 1
)
if not exist "%SRV%\harvester.db" (
  echo [start] database not found: %SRV%\harvester.db
  pause
  exit /b 1
)

rem cards_pending: the card validation endpoint needs --cards-root
rem (missing flag shows "not configured" on triage/report pages).
if not exist "%SRV%\cards_pending" mkdir "%SRV%\cards_pending"

start "harvester api-serve :8765" /D "%SRV%" "%PY%" -m harvester api-serve --db "%SRV%\harvester.db" --cards-root "%SRV%\cards_pending"
start "harvester-view :8088" /D "%~dp0" "%PY%" view.py
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8088/
echo [start] api-serve + view launched. Close both windows to stop.
