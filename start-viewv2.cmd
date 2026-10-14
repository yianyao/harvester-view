rem ============================================================
rem  harvester-view P2 starter: view.py (8088) + actions-serve (8766)
rem  plus upstream api-serve (8765). ASCII only, CRLF only.
rem  P2-1: viewserver.py is merged into actions/actionsserve.py
rem  (same port 8766, one process). This script now starts
rem  actionsserve directly and passes the meta db paths explicitly.
rem ============================================================
setlocal
set "SRV=%~dp0..\session-harvester"
set "BUNDLED=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  if exist "%BUNDLED%" set "PY=%BUNDLED%"
)
if not defined PY (
  echo [start] no Python ^>= 3.10 found.
  pause
  exit /b 1
)
if not exist "%SRV%\harvester.db" (
  echo [start] database not found: %SRV%\harvester.db
  pause
  exit /b 1
)
rem v0.66 master-in-project (user ruling 2026-10-14: the data MASTER lives
rem in the repo's knowledge\ dir and migrates WITH the project; the WorkBuddy
rem dir is only a publish MIRROR fed by: python -m harvester kb-publish).
rem Env vars still win for exotic layouts, e.g.
rem   set CARDS_ROOT=D:\elsewhere\cards
if not defined CARDS_ROOT set "CARDS_ROOT=%SRV%\knowledge\cards"
if not defined CHAIN_ROOT set "CHAIN_ROOT=%SRV%\knowledge\topics"
if not exist "%CARDS_ROOT%" mkdir "%CARDS_ROOT%"
start "harvester api-serve :8765" /D "%SRV%" %PY% -m harvester api-serve --db "%SRV%\harvester.db" --cards-root "%CARDS_ROOT%" --topics-meta "%SRV%\topics_meta.db" --keywords-meta "%SRV%\keywords_meta.db" --suggestions-meta "%SRV%\suggestions_meta.db"
rem P2: local action surface (whitelisted A-tier actions) + read-only data.
rem v0.49：**必须传 --exports-root**。会话抑制（session_suppress）要清掉该 sid 的
rem exports 旧副本；不传的话 cfg.exports_root=None，预演只能警告"本次不清副本"，
rem 而旧副本还在 → 下次 sync/update 会把它重新索引回来（= 抑制看起来"没生效"）。
rem `tests/test_v41_suppress_view.py` 有一条门读本文件，断言这一行在。
start "harvester actions-serve :8766" /D "%~dp0" %PY% -m actions.actionsserve --db "%SRV%\harvester.db" --cards-root "%CARDS_ROOT%" --topics-meta "%SRV%\topics_meta.db" --artifacts-meta "%SRV%\artifacts_meta.db" --keywords-meta "%SRV%\keywords_meta.db" --suggestions-meta "%SRV%\suggestions_meta.db" --chain-root "%CHAIN_ROOT%" --exports-root "%SRV%\exports"
start "harvester-view :8088" /D "%~dp0" %PY% view.py --no-open
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8088/
echo [start] api-serve(8765) + actions-serve(8766) + view(8088) launched.
echo [start] Close all three console windows to stop.
