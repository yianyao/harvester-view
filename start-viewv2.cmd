rem ============================================================
rem  harvester-view starter: view (8088) + actions-serve (8766)
rem  plus upstream api-serve (8765). ASCII only, CRLF only.
rem  cmd.exe parses .cmd files in the ANSI (GBK) code page: any
rem  non-ASCII byte here turns to mojibake and can even split a
rem  line into a bogus command (user screenshot 2026-10-15).
rem  Keep every line of this file pure ASCII. Explanations for
rem  humans live in docs / the view UI, not in this file.
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
rem v0.66 master-in-project: the data MASTER lives in the repo knowledge
rem dir and migrates WITH the project; the WorkBuddy dir is only a publish
rem MIRROR fed by: python -m harvester kb-publish
rem Env vars still win for exotic layouts, e.g.  set CARDS_ROOT=D:/elsewhere/cards
if not defined CARDS_ROOT set "CARDS_ROOT=%SRV%\knowledge\cards"
if not defined CHAIN_ROOT set "CHAIN_ROOT=%SRV%\knowledge\topics"
if not exist "%CARDS_ROOT%" mkdir "%CARDS_ROOT%"
echo [1/3] starting api-serve on port 8765 ... one new window per service
rem v0.81 P0-2/P0-3: --reviews-meta and --chain-root pinned EXPLICITLY.
rem Both defaults are cwd-relative ("cards_meta.db" / "knowledge/topics"),
rem so starting from another directory silently loses the human review
rem ledger and the chain docs (the page would show a false empty state).
start "harvester api-serve :8765" /D "%SRV%" %PY% -m harvester api-serve --db "%SRV%\harvester.db" --cards-root "%CARDS_ROOT%" --topics-meta "%SRV%\topics_meta.db" --keywords-meta "%SRV%\keywords_meta.db" --suggestions-meta "%SRV%\suggestions_meta.db" --chain-root "%CHAIN_ROOT%" --reviews-meta "%SRV%\cards_meta.db" --corpus-watermark
rem v0.49: --exports-root is REQUIRED. session_suppress must purge stale
rem export copies of a suppressed sid; without it cfg.exports_root=None and
rem the copies come back on next sync. Gate: tests/test_v41_suppress_view.py
rem v0.81 P0-2: --reviews-meta pinned (default None = card_review fails at
rem preview time: "reviews_meta not configured"). v0.81 P1-4:
rem --corpus-watermark enables the real STALE verdict (corpus newer than
rem the indexed watermark) on the timeline/overview endpoints.
echo [2/3] starting actions-serve on port 8766 ...
start "harvester actions-serve :8766" /D "%~dp0" %PY% -m actions.actionsserve --db "%SRV%\harvester.db" --cards-root "%CARDS_ROOT%" --topics-meta "%SRV%\topics_meta.db" --artifacts-meta "%SRV%\artifacts_meta.db" --keywords-meta "%SRV%\keywords_meta.db" --suggestions-meta "%SRV%\suggestions_meta.db" --chain-root "%CHAIN_ROOT%" --exports-root "%SRV%\exports" --reviews-meta "%SRV%\cards_meta.db" --corpus-watermark
echo [3/3] starting view on port 8088 ...
start "harvester-view :8088" /D "%~dp0" %PY% view.py --no-open
echo [wait] giving the three services 3 seconds to come up ...
timeout /t 3 /nobreak >nul
call :check_port 8765 api-serve
call :check_port 8766 actions-serve
call :check_port 8088 view
start "" http://127.0.0.1:8088/
echo.
echo [done] Browser is opening http://127.0.0.1:8088/ - if not, open it by hand.
echo [done] A FAIL line above means that service died: read its console window.
echo [done] To stop everything: close the three harvester console windows.
pause
exit /b 0

:check_port
netstat -ano | find ":%1 " | find "LISTENING" >nul
if errorlevel 1 (
  echo [FAIL] %2 did NOT come up on port %1 - check its window for the error.
) else (
  echo [ OK ] %2 is listening on port %1.
)
goto :eof
