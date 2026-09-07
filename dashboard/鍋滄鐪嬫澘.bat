@echo off
rem Stop dashboard server by PID file
cd /d "%~dp0"

set "PID="
if exist "%~dp0dashboard.pid" set /p PID=<"%~dp0dashboard.pid"
if defined PID (
    taskkill /f /pid %PID% >nul 2>nul
    if errorlevel 1 (
        echo 未找到对应进程, 看板可能已经停止。
    ) else (
        echo 看板已停止。
    )
    del "%~dp0dashboard.pid" >nul 2>nul
) else (
    echo 未找到 PID 文件, 看板可能未在运行。
    echo 提示: 若是旧版方式前台运行 python dashboard.py, 请直接关闭那个命令行窗口。
)
pause
