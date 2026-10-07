@echo off
rem ============================================================
rem  harvester-view one-click starter
rem  Starts BOTH: api-serve (upstream, port 8765) + view (8088),
rem  then opens the browser. Close the two console windows to stop.
rem
rem  NOTE: the python path below is the WorkBuddy bundled
rem  interpreter. This machine has NO standalone Python on the
rem  persistent PATH (verified 2026-10-07: user PATH / system
rem  PATH / python.org / conda all empty; only WorkBuddy's
rem  managed python exists and it is injected only inside
rem  WorkBuddy sessions). If that ever changes, update PY here.
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

start "harvester api-serve :8765" /D "%SRV%" "%PY%" -m harvester api-serve --db "%SRV%\harvester.db"
start "harvester-view :8088" /D "%~dp0" "%PY%" view.py
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:8088/
echo [start] api-serve + view launched. Close both windows to stop.
