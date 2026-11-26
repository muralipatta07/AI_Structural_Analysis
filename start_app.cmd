@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" app.py
) else (
    py -3.12 app.py
)
if errorlevel 1 pause
