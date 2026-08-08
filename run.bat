@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py %*
) else (
    python main.py %*
)
if errorlevel 1 (
    echo.
    echo Neu bao loi thieu package, cai de truoc:
    echo   python -m pip install -r requirements.txt
    pause
)
