@echo off
set "ROOT=%~dp0..\.."
set "AGENT_CONTROL_HOST=127.0.0.1"
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
"%ROOT%\.venv\Scripts\python.exe" "%ROOT%\hub\hub.py" >> "%ROOT%\logs\hub.log" 2>> "%ROOT%\logs\hub.err.log"
