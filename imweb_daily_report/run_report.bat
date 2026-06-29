@echo off
setlocal

cd /d C:\imweb_daily_report

set "PYTHON_CMD="
where py >nul 2>nul
if %errorlevel%==0 set "PYTHON_CMD=py -3.11"

if "%PYTHON_CMD%"=="" (
  where python >nul 2>nul
  if %errorlevel%==0 set "PYTHON_CMD=python"
)

if "%PYTHON_CMD%"=="" (
  where python3 >nul 2>nul
  if %errorlevel%==0 set "PYTHON_CMD=python3"
)

if "%PYTHON_CMD%"=="" (
  echo Python 3.11 or newer was not found. Please install Python and run again.
  exit /b 1
)

if not exist .venv (
  %PYTHON_CMD% -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py

endlocal
