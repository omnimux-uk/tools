@echo off
rem Windows launcher. The Android target is the only one that can run here --
rem iOS simulators are a macOS framework -- and it needs no pip packages at
rem all, just adb and scrcpy on PATH. So unlike the POSIX launcher this does
rem not insist on a virtualenv: it uses one if install.sh built it under WSL
rem or Git Bash, and falls back to the system interpreter if not.
setlocal
set "HERE=%~dp0"
set "PY=%HERE%.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
cd /d "%HERE%"
"%PY%" -m bridge %*
