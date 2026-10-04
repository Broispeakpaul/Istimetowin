@echo off
REM One-time setup on Windows: creates .venv with Python 3.11 and installs the requirements, then runs the tests.
cd /d "%~dp0.."
py -3.11 -m venv .venv || (echo Python 3.11 not found. Install it from python.org and tick "Add to PATH". & exit /b 1)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m pytest
python -m screener demo
