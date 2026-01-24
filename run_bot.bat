@echo off
if not exist "venv\Scripts\activate" (
    echo [ERROR] Virtual environment (venv) not found!
    echo Please create it first using: python -m venv venv
    pause
    exit /b
)

echo [OK] Activating virtual environment...
call venv\Scripts\activate

echo Starting Siphon Video Scraper Bot...
python bot.py
pause
