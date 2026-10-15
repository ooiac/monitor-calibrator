@echo off
rem Starts Monitor Calibrator.
rem The first time, it checks what's needed and installs what's missing. Setup then saves
rem "setup_done": true in the settings file, and from then on this just starts the app.
setlocal
cd /d "%~dp0"

rem Find a real Python. "python" can be a Microsoft Store placeholder, so ask each one for its version.
set "PY="
py -3 --version >nul 2>nul && (set "PY=py -3" & set "PYW=pyw -3")
if not defined PY python --version >nul 2>nul && (set "PY=python" & set "PYW=pythonw")
if not defined PY goto no_python

set "DATA=%LOCALAPPDATA%\MonitorCalibrator"
if defined MONITOR_CALIBRATOR_HOME set "DATA=%MONITOR_CALIBRATOR_HOME%"
findstr /c:"\"setup_done\": true" "%DATA%\settings.json" >nul 2>nul && goto start

%PY% install.py --first-run
if errorlevel 1 (
    echo.
    pause
    exit /b 1
)

:start
start "" %PYW% "%~dp0calibrate.py" %*
exit /b 0

:no_python
echo Monitor Calibrator needs Python 3.10 or newer, and it isn't installed yet.
echo.
where winget >nul 2>nul || goto manual
choice /c yn /m "Install Python 3.12 now with winget"
if errorlevel 2 goto manual
winget install --exact --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
echo.
echo When it's done, close this window and double-click Calibrate.bat again.
pause
exit /b 1

:manual
echo Download it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
echo Then double-click Calibrate.bat again.
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1
