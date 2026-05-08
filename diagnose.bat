@echo off
cd /d "%~dp0"

echo =====================================
echo   DIAGNOSE - take a screenshot
echo =====================================
echo.

echo [1] Current folder:
echo     %CD%
echo.

echo [2] Required files:
if exist app.py (echo     [O] app.py) else (echo     [X] app.py MISSING)
if exist requirements.txt (echo     [O] requirements.txt) else (echo     [X] requirements.txt MISSING)
if exist src\models.py (echo     [O] src\models.py) else (echo     [X] src\models.py MISSING)
if exist .venv (echo     [O] .venv exists - already installed) else (echo     [.] .venv not yet - first run needed)
echo.

echo [3] Python version:
python --version 2>nul
if errorlevel 1 (
    echo     [X] Python is NOT installed or NOT in PATH.
    echo     Fix: install Python 3.10+ and check "Add to PATH".
) else (
    echo     [O] Python OK
)
echo.

echo [4] Python install location:
where python 2>nul
if errorlevel 1 echo     [X] python command not found in PATH
echo.

echo [5] pip version:
pip --version 2>nul
if errorlevel 1 echo     [X] pip command not found
echo.

echo [6] Streamlit installed in .venv?:
if exist .venv\Scripts\streamlit.exe (
    echo     [O] streamlit installed
) else (
    if exist .venv (
        echo     [.] .venv exists but streamlit NOT installed yet
    ) else (
        echo     [.] .venv not created yet - run.bat needs first run
    )
)
echo.

echo =====================================
echo   END - send the [O] / [X] / [.] marks above
echo =====================================
pause
