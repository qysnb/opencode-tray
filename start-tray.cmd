@echo off
rem Launch the OpenCode tray indicator without a console window.
rem ASCII only on purpose: cmd.exe reads batch files using the ANSI code page.
setlocal
set "SCRIPT=%~dp0opencode_tray.pyw"

if not exist "%SCRIPT%" (
  echo Cannot find "%SCRIPT%"
  pause
  exit /b 1
)

rem Resolve pythonw through PATH. Note this is only a convenience launcher: the
rem desktop shortcut created by --install-shortcut pins the interpreter
rem explicitly, which is more reliable on machines with several Pythons.
for %%P in (pythonw.exe) do set "PYW=%%~$PATH:P"
if not defined PYW (
  echo pythonw.exe was not found on PATH.
  echo Install Python 3, then run:
  echo     python "%SCRIPT%" --install-shortcut
  pause
  exit /b 1
)

rem pythonw has no console, so a missing dependency would fail silently.
rem Check first and say what to do instead.
"%PYW%" -c "import pystray, PIL" 2>nul
if errorlevel 1 (
  echo The pythonw on PATH cannot import pystray / Pillow.
  echo Install them with:
  echo     "%PYW%" -m pip install pystray Pillow
  pause
  exit /b 1
)

start "" "%PYW%" "%SCRIPT%"
endlocal
