@echo off
rem Launch the OpenCode server tray indicator (no console window).
rem ASCII only on purpose: cmd.exe reads batch files using the ANSI code page.
setlocal
set "SCRIPT=%~dp0opencode_tray.pyw"

if not exist "%SCRIPT%" (
  echo Cannot find "%SCRIPT%"
  pause
  exit /b 1
)

where pythonw >nul 2>nul
if errorlevel 1 (
  echo pythonw not found on PATH - falling back to python ^(a console window will stay open^).
  start "" python "%SCRIPT%"
) else (
  start "" pythonw "%SCRIPT%"
)

endlocal
