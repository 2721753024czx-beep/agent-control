@echo off
set "ROOT=%~dp0..\.."
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
"%ROOT%\.venv\Scripts\python.exe" "%ROOT%\node\node.py" >> "%ROOT%\logs\node.log" 2>> "%ROOT%\logs\node.err.log"
