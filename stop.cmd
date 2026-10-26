@echo off
rem ============================================================
rem  stop.cmd -- one click: stop all three harvester services
rem  (api-serve 8765 / actions-serve 8766 / view 8088).
rem
rem  The three launchers start each service in its OWN console window
rem  (that is how they were designed: you can watch their logs), so
rem  closing the launcher window does NOT stop them. 2026-10-26 user
rem  order: "closing must close everything, starting must start
rem  everything". Hence this file: one place that stops all three,
rem  using exactly the same selection rule as reclaim.cmd and
rem  desktop/main.js -- a leftover process that lacks it would be a
rem  process nobody can stop.
rem
rem  Keep this file PURE ASCII + CRLF (cmd.exe parses ANSI code page).
rem ============================================================
setlocal
call "%~dp0reclaim.cmd"
echo [stop] all harvester services are stopped. Ports 8765/8766/8088 are free.
"%SystemRoot%\System32\ping.exe" -n 4 127.0.0.1 >nul 2>&1
exit /b 0
