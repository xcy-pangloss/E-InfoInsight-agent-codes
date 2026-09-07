@echo off
rem Start dashboard without console window, then open browser
cd /d "%~dp0"

set "OLDPID="
if exist "%~dp0dashboard.pid" set /p OLDPID=<"%~dp0dashboard.pid"
if defined OLDPID (
    tasklist /fi "pid eq %OLDPID%" 2>nul | findstr /i "python" >nul
    if not errorlevel 1 (
        echo 看板已在运行, 直接打开浏览器...
        start "" "http://127.0.0.1:8642"
        exit /b 0
    )
    del "%~dp0dashboard.pid" >nul 2>nul
)

set NO_OPEN=1
start "" pythonw "%~dp0dashboard.py"
ping -n 3 127.0.0.1 >nul
start "" "http://127.0.0.1:8642"
