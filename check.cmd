@echo off
setlocal
cd /d "%~dp0"
set "CHECK_PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "CHECK_PYTHON=%~dp0.venv\Scripts\python.exe"
"%CHECK_PYTHON%" -m ruff check .
if errorlevel 1 exit /b %errorlevel%
"%CHECK_PYTHON%" -m coverage run -m unittest discover -s tests
if errorlevel 1 exit /b %errorlevel%
"%CHECK_PYTHON%" -m coverage report
if errorlevel 1 exit /b %errorlevel%
