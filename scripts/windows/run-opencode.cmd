@echo off
set "ROOT=%~dp0..\.."
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
"%ROOT%\.venv\Scripts\python.exe" "%ROOT%\scripts\run_safe_opencode.py" >> "%ROOT%\logs\opencode.log" 2>> "%ROOT%\logs\opencode.err.log"
