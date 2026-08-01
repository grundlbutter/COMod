@echo off
title CO Asset Viewer
cd /d "%~dp0"

echo.
echo   Classic Conquer 2.0 - Asset Viewer
echo   ----------------------------------
echo   Starting server, your browser will open automatically.
echo   Close this window (or press Ctrl+C) to stop it.
echo.

py -3 "tools\coviewer.py" %*

if errorlevel 1 (
  echo.
  echo   *** The viewer exited with an error. Message above. ***
  echo.
  pause
)
