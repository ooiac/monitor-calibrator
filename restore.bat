@echo off
rem Puts every screen back the way it was before the last change, without opening the app.
rem Run "restore.bat --original" to go back to how things were before this app ever changed them.
cd /d "%~dp0"
where py >nul 2>nul && (py -3 calibrate.py --restore all %*) || (python calibrate.py --restore all %*)
pause
