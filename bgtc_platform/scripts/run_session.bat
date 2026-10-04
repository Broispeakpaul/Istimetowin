@echo off
REM Usage: run_session.bat us|asia|europe [extra args, e.g. --before-close 30]
REM Runs one daily screen and appends output to logs\<session>.log. Recommendations only; no orders are placed.
setlocal
cd /d "%~dp0.."
if not exist logs mkdir logs
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
echo ==== %DATE% %TIME% session=%1 %2 %3 ==== >> logs\%1.log
python -m screener run --session %* >> logs\%1.log 2>&1
endlocal
