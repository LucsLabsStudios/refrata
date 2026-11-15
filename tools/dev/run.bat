@echo off
cd /d "%~dp0..\.."
start "" pythonw src\refrata\__main__.py %*
