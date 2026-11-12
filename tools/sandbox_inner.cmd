@echo off
rem Runs inside Windows Sandbox at logon (tools\sandbox.py writes the configuration).
rem C:\refrata = the repository (read-only), C:\py = the host's Python (read-only), C:\out = results.
setlocal
set PATH=C:\py;C:\py\Scripts;%PATH%
set PYTHONDONTWRITEBYTECODE=1
set PYTHONUTF8=1
rem the repository is read-only: work on a copy (tests write fixtures and reports next to themselves)
robocopy C:\refrata C:\work /E /NFL /NDL /NJH /NJS /XD .git build vendor node_modules __pycache__ /XF *.pyc >nul
robocopy C:\refrata\dist\Refrata C:\work\dist\Refrata /E /NFL /NDL /NJH /NJS >nul
if exist C:\refrata\vendor robocopy C:\refrata\vendor C:\work\vendor /E /NFL /NDL /NJH /NJS >nul
cd /d C:\work
python tools\sandbox_inner.py > C:\out\log.txt 2>&1
