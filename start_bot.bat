@echo off
title Bot Discord Auto-Restart
:loop
echo Memulai bot...
python main.py
echo.
echo Bot berhenti atau crash. Restart dalam 5 detik...
timeout /t 5
goto loop