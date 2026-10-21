@echo off
set "ROOT=%~dp0..\.."
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
"%ROOT%\.venv\Scripts\python.exe" "%ROOT%\scripts\history_loop.py" --interval 300 >> "%ROOT%\logs\history.log" 2>> "%ROOT%\logs\history.err.log"
