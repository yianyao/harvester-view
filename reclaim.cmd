@echo off
rem ============================================================
rem  reclaim.cmd -- stop EVERY service of this project that a
rem  previous session left behind: api-serve 8765, actions-serve
rem  8766, view 8088.
rem
rem  Why this file exists (2026-10-26, user report):
rem  the page was running NEW code while the back end was still the
rem  OLD process from an earlier window. Clicking an action that the
rem  new registry declares (group=enum) failed with a message that
rem  told the user to "restart both windows" -- and restarting one
rem  window is exactly what people forget. So the launchers now call
rem  this file first: every start begins from a clean slate.
rem
rem  Selection rule (deliberately identical to desktop/main.js
rem  reclaimPorts()): process name python/pythonw AND command line
rem  containing "-m harvester" (all upstream subcommands),
rem  "actionsserve" (the action face) or "view.py" (this view).
rem  NEVER touch node/electron -- the desktop shell itself is
rem  electron, killing it would crash the window that asked for this.
rem
rem  Usage:  call reclaim.cmd   <- launchers (3 files) and stop.cmd
rem  Keep this file PURE ASCII + CRLF: cmd.exe parses .cmd in the
rem  ANSI (GBK) code page, a non-ASCII byte here turns to mojibake
rem  and can split a line into a bogus command.
rem ============================================================
setlocal
echo [reclaim] clearing leftover harvester services (old code must not survive)...
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$tk = Join-Path $env:SystemRoot 'System32\taskkill.exe'; $sel = Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|pythonw)\.exe$' -and (($_.CommandLine -match '-m harvester') -or ($_.CommandLine -match 'actionsserve') -or ($_.CommandLine -match '(^|[\\/\s])view\.py')) }; $n = 0; foreach ($p in $sel) { $n++; $c = '' + $p.CommandLine; if ($c.Length -gt 78) { $c = $c.Substring(0,78) }; Write-Host ('[reclaim] stop ' + $p.Name + ' pid=' + $p.ProcessId + ' :: ' + $c); $null = & $tk /PID $p.ProcessId /T /F 2>&1 }; Write-Host ('[reclaim] stopped ' + $n + ' leftover process(es)')"
rem Give Windows a moment to release the three listening sockets, so the
rem launcher that called us does not race with a dying process.
"%SystemRoot%\System32\ping.exe" -n 3 127.0.0.1 >nul 2>&1
exit /b 0
