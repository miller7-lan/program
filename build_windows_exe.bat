@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
title Profit Helper Windows Build

set "LOG_FILE=%SCRIPT_DIR%build_windows_exe.log"
set "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
set "PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn"
set "VENV_DIR=%SCRIPT_DIR%.venv-win"
set "DIST_EXE=%SCRIPT_DIR%dist\ProfitHelper.exe"
set "PYTHON_CMD="
set "BUILD_FAILED="

break > "%LOG_FILE%"
call :log ========================================
call :log Profit Helper Windows build started
call :log Working directory: %SCRIPT_DIR%
call :log Log file: %LOG_FILE%
call :log ========================================

echo.
echo Profit Helper Windows Build
echo Log file: %LOG_FILE%
echo.

if exist "%SystemRoot%\py.exe" (
    set "PYTHON_CMD=%SystemRoot%\py.exe -3"
) else (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    call :fail Python 3 was not found. Please install Python 3.11+ and enable "Add Python to PATH".
    goto :finish
)

call :log Python command: %PYTHON_CMD%

echo [1/6] Checking Python...
call %PYTHON_CMD% --version >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    call :fail Python could not start.
    goto :finish
)

echo [2/6] Creating virtual environment...
if not exist "%VENV_DIR%\Scripts\python.exe" (
    call :log Creating virtual environment: %VENV_DIR%
    call %PYTHON_CMD% -m venv "%VENV_DIR%" >> "%LOG_FILE%" 2>&1
    if errorlevel 1 (
        call :fail Failed to create virtual environment.
        goto :finish
    )
) else (
    call :log Reusing virtual environment: %VENV_DIR%
)

echo [3/6] Activating virtual environment...
call "%VENV_DIR%\Scripts\activate.bat" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    call :fail Failed to activate virtual environment.
    goto :finish
)

echo [4/6] Upgrading packaging tools...
python -m pip install --upgrade pip setuptools wheel -i %PIP_INDEX_URL% --trusted-host %PIP_TRUSTED_HOST% >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    call :fail Failed to upgrade pip/setuptools/wheel.
    goto :finish
)

echo [5/6] Installing dependencies from Tsinghua mirror...
pip install -r requirements.txt -i %PIP_INDEX_URL% --trusted-host %PIP_TRUSTED_HOST% >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    call :fail Failed to install requirements.txt.
    goto :finish
)

echo [6/6] Building EXE...
if exist "%SCRIPT_DIR%build" rmdir /s /q "%SCRIPT_DIR%build" >> "%LOG_FILE%" 2>&1
if exist "%SCRIPT_DIR%dist" rmdir /s /q "%SCRIPT_DIR%dist" >> "%LOG_FILE%" 2>&1

pyinstaller --noconfirm --clean --windowed --onefile --name "ProfitHelper" --icon "assets\app_icon.ico" --add-data "assets;assets" main.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    call :fail PyInstaller build failed.
    goto :finish
)

if not exist "%DIST_EXE%" (
    call :fail Build finished but dist\ProfitHelper.exe was not found.
    goto :finish
)

call :log Build succeeded: %DIST_EXE%
echo.
echo Build succeeded:
echo %DIST_EXE%
echo.
echo The database file family_ledger.db will be created next to the EXE on first run.
goto :finish

:fail
set "BUILD_FAILED=1"
echo.
echo [ERROR] %*
echo Check log: %LOG_FILE%
echo.
call :log [ERROR] %*
goto :eof

:log
echo %*
>> "%LOG_FILE%" echo %*
goto :eof

:finish
echo.
if defined BUILD_FAILED (
    echo Build did not finish successfully.
    echo Please send me this log file:
    echo %LOG_FILE%
) else (
    echo Press any key to close this window.
)
echo.
pause
exit /b 0
