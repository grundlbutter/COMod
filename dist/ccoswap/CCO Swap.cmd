@echo off
rem CCO Swap -- double-click to run.
rem
rem Two lines matter.
rem
rem   APPDATA  Settings normally live in %APPDATA%\co-client-re\config.json,
rem            which every copy on the machine shares. Pointing it at the
rem            folder beside this script makes this copy self-contained: its
rem            own settings, its own first run, unable to disturb any other.
rem
rem   python\  The runtime travels with the package, so this needs no Python
rem            installed. See THIRD-PARTY.md for what is bundled and its terms.
setlocal
set "APPDATA=%~dp0config"
cd /d "%~dp0"

if exist "%~dp0python\python.exe" (
  "%~dp0python\python.exe" launch.py
) else (
  echo   [note] bundled runtime missing; falling back to system Python.
  py -3 launch.py
)
if errorlevel 1 pause
endlocal
