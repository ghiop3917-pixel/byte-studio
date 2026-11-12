@echo off
setlocal
cd /d "%~dp0"
python -m ruff check .
if errorlevel 1 exit /b %errorlevel%
python -m coverage run -m unittest discover -s tests
if errorlevel 1 exit /b %errorlevel%
python -m coverage report
if errorlevel 1 exit /b %errorlevel%
