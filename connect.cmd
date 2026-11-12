@echo off
chcp 65001 >nul
python -X utf8 "%~dp0connect.py"
pause
