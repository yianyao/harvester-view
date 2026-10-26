@echo off
rem ============================================================
rem  harvester-view starter (local machine default launcher)
rem  Detection order: 'py -3' -> 'python' -> 'uv python find' ->
rem  fallback: WorkBuddy bundled interpreter (this machine only).
rem  Keep this file PURE ASCII + CRLF. Echo lines: no double quotes.
rem ============================================================
setlocal
set "SRV=%~dp0..\session-harvester"
set "BUNDLED=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"

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
  if exist "%BUNDLED%" set "PY=%BUNDLED%"
)
if not defined PY (
  echo [start] no Python ^>= 3.10 found. Tried 'py -3', 'python', 'uv',
  echo         and the bundled interpreter is missing too.
  pause
  exit /b 1
)

if not exist "%SRV%\harvester.db" (
  echo [start] database not found: %SRV%\harvester.db
  pause
  exit /b 1
)
rem v0.22 P0-4: cards-root aligned to the REAL card library (knowledge\cards).
rem The old cards_pending stub directory is deprecated (0 files, G3 showed
rem an empty cabinet); the main library is now the validation target.
rem v0.66 master-in-project: cards master = repo knowledge\cards (migrates
rem with the project); WorkBuddy dir is a publish mirror (harvester kb-publish).
rem Env var CARDS_ROOT still wins for exotic layouts.
if not defined CARDS_ROOT set "CARDS_ROOT=%SRV%\knowledge\cards"
if not exist "%CARDS_ROOT%" mkdir "%CARDS_ROOT%"
rem v0.81 P0-3: chain-root pinned EXPLICITLY. api-serve's default is the
rem cwd-relative "knowledge/topics" - starting the service from any other
rem directory 404s the chain endpoint and the page shows a FALSE empty
rem state ("this topic has no chain doc yet"). Env var wins for exotic
rem layouts (portability rule: machine paths must be overridable).
if not defined CHAIN_ROOT set "CHAIN_ROOT=%SRV%\knowledge\topics"

rem v0.22 topics/keywords/suggestions: meta libs live next to harvester.db.
rem api-serve degrades gracefully if one is missing (hint, no crash).
rem v0.81 P0-2/P0-3: --reviews-meta and --chain-root are pinned EXPLICITLY.
rem Both defaults are cwd-relative ("cards_meta.db" / "knowledge/topics"),
rem so starting from another directory silently loses the human review
rem ledger (card_review would fail) and the chain docs (false empty state).
rem 2026-10-26 (user order): a start must begin from a CLEAN slate.
rem Clear any service left over from a previous session/session window
rem before starting. Without this the page can be new code while the
rem back end is an old process -- the action then fails with a message
rem that blames "old process", and one forgotten window is enough to
rem reproduce it. Selection rule identical to desktop/main.js.
call "%~dp0reclaim.cmd"
start "harvester api-serve :8765" /D "%SRV%" %PY% -m harvester api-serve --db "%SRV%\harvester.db" --cards-root "%CARDS_ROOT%" --topics-meta "%SRV%\topics_meta.db" --keywords-meta "%SRV%\keywords_meta.db" --suggestions-meta "%SRV%\suggestions_meta.db" --chain-root "%CHAIN_ROOT%" --reviews-meta "%SRV%\cards_meta.db" --corpus-watermark
rem v0.81 P0-1: the VIEW is a THREE-service system. Without actions-serve
rem the landing page (today workbench), command console, work items,
rem attribution desk, topic reading/dualtrack faces, knowledge graph and
rem ALL B-tier write actions are unreachable (a yellow banner only).
rem --reviews-meta: without it card_review fails at preview time
rem ("reviews_meta not configured") - the button goes red on click.
start "harvester actions-serve :8766" /D "%~dp0" %PY% -m actions.actionsserve --db "%SRV%\harvester.db" --cards-root "%CARDS_ROOT%" --topics-meta "%SRV%\topics_meta.db" --artifacts-meta "%SRV%\artifacts_meta.db" --keywords-meta "%SRV%\keywords_meta.db" --suggestions-meta "%SRV%\suggestions_meta.db" --chain-root "%CHAIN_ROOT%" --reviews-meta "%SRV%\cards_meta.db" --exports-root "%SRV%\exports" --corpus-watermark
rem --no-open: view.py must NOT open its own tab; the line below opens exactly one.
start "harvester-view :8088" /D "%~dp0" %PY% view.py --no-open
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8088/
echo [start] api-serve + actions-serve + view launched with: %PY%
echo [start] Closing THIS window does NOT stop the services.
echo [start] Stop all three in one click: double-click stop.cmd.
