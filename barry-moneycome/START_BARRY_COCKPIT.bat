@echo off
setlocal
title Barry Sentinel V7 - Cockpit
cd /d "%~dp0"

set "V7PY=%USERPROFILE%\Documents\Barry Data & Analytics\BARRY SENTINEL\01_Aktuelle_Arbeitsversion\Barry_Sentinel_V7_IBKR_Paper\.venv\Scripts\python.exe"

echo ==============================================================
echo BARRY SENTINEL V7 - COCKPIT START
echo ==============================================================
echo [INFO] Dieses Cockpit ist READ ONLY.
echo [INFO] Keine Broker-Order. Keine Auszahlung. Kein Live-Trading.
echo.

if exist "%V7PY%" (
  echo [OK] V7 Python-Umgebung gefunden.
  "%V7PY%" "%~dp0server.py"
  goto :end
)

where py >nul 2>nul
if %errorlevel%==0 (
  echo [INFO] V7 Python nicht am Standardpfad gefunden. Nutze Windows Python Launcher.
  py -3 "%~dp0server.py"
  goto :end
)

where python >nul 2>nul
if %errorlevel%==0 (
  echo [INFO] Nutze python aus PATH.
  python "%~dp0server.py"
  goto :end
)

echo [FEHLER] Python wurde nicht gefunden.
echo [HINWEIS] Der V7-Ordner bleibt unveraendert.
pause

:end
endlocal
