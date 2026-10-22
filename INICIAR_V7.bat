@echo off
cd /d "%~dp0"
python tools\prepare_ui.py
python unified_v7.py
pause
