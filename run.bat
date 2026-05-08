@echo off
cd /d "%~dp0"

echo =====================================
echo   Modular Transport Simulator
echo =====================================
echo.

if not exist .venv\ (
    echo [First run] Creating virtual env...
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo [ERROR] Python is NOT installed or NOT in PATH.
        echo Install Python 3.10+ from https://www.python.org/downloads/
        echo IMPORTANT: check "Add Python to PATH" during install.
        echo.
        pause
        exit /b 1
    )
    call .venv\Scripts\activate.bat
    echo.
    echo [First run] Installing libraries... 2-3 min, internet required.
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [ERROR] Library install failed. Check internet and retry.
        echo.
        pause
        exit /b 1
    )
    echo.
    echo [Done] Setup complete.
) else (
    call .venv\Scripts\activate.bat
)

echo.
echo =====================================
echo   Browser will open at http://localhost:8501
echo   Stop server: Ctrl+C
echo =====================================
echo.

streamlit run app.py

pause
