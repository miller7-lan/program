@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"
title 利润助手 Windows 一键打包

set "LOG_FILE=%~dp0build_windows_exe.log"
set "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
set "PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn"
set "VENV_DIR=%~dp0.venv-win"
set "DIST_EXE=%~dp0dist\利润助手.exe"
set "PYTHON_CMD="
set "BUILD_FAILED="

break > "%LOG_FILE%"
call :log ========================================
call :log 利润助手 Windows 一键打包开始
call :log 当前目录: %~dp0
call :log 日志文件: %LOG_FILE%
call :log ========================================

echo.
echo 利润助手 Windows 一键打包
echo 日志文件：%LOG_FILE%
echo.

if exist "%SystemRoot%\py.exe" (
    set "PYTHON_CMD=%SystemRoot%\py.exe -3"
) else (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    call :fail "未检测到 Python 3。请先安装 Python 3.11 或以上版本，并勾选 Add Python to PATH。"
    goto :finish
)

call :log 使用 Python 命令: %PYTHON_CMD%
echo [1/6] 检查 Python...
call %PYTHON_CMD% --version >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    call :fail "Python 无法启动，请检查本机 Python 安装。"
    goto :finish
)

echo [2/6] 创建虚拟环境...
if not exist "%VENV_DIR%\Scripts\python.exe" (
    call :log 开始创建虚拟环境: %VENV_DIR%
    call %PYTHON_CMD% -m venv "%VENV_DIR%" >> "%LOG_FILE%" 2>&1
    if errorlevel 1 (
        call :fail "创建虚拟环境失败。"
        goto :finish
    )
) else (
    call :log 复用已存在的虚拟环境: %VENV_DIR%
)

echo [3/6] 激活虚拟环境...
call "%VENV_DIR%\Scripts\activate.bat" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    call :fail "激活虚拟环境失败。"
    goto :finish
)

echo [4/6] 升级基础工具...
python -m pip install --upgrade pip setuptools wheel -i %PIP_INDEX_URL% --trusted-host %PIP_TRUSTED_HOST% >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    call :fail "升级 pip / setuptools / wheel 失败。"
    goto :finish
)

echo [5/6] 安装项目依赖（清华镜像）...
pip install -r requirements.txt -i %PIP_INDEX_URL% --trusted-host %PIP_TRUSTED_HOST% >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    call :fail "安装 requirements.txt 失败。"
    goto :finish
)

echo [6/6] 生成 EXE...
if exist "%~dp0build" rmdir /s /q "%~dp0build" >> "%LOG_FILE%" 2>&1
if exist "%~dp0dist" rmdir /s /q "%~dp0dist" >> "%LOG_FILE%" 2>&1

pyinstaller --noconfirm --clean --windowed --onefile --name "利润助手" --icon "assets\app_icon.ico" --add-data "assets;assets" main.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    call :fail "PyInstaller 打包失败。"
    goto :finish
)

if not exist "%DIST_EXE%" (
    call :fail "打包完成后未找到 dist\利润助手.exe。"
    goto :finish
)

call :log 打包成功: %DIST_EXE%
echo.
echo 打包成功：
echo %DIST_EXE%
echo.
echo 首次运行时会在 exe 同级目录自动生成 family_ledger.db
goto :finish

:fail
set "BUILD_FAILED=1"
echo.
echo [错误] %~1
echo 详细日志请看：%LOG_FILE%
echo.
call :log [错误] %~1
goto :eof

:log
echo %~1
>> "%LOG_FILE%" echo %~1
goto :eof

:finish
echo.
if defined BUILD_FAILED (
    echo 打包未完成，请把日志文件发给我：
    echo %LOG_FILE%
) else (
    echo 可以关闭窗口了，或者按任意键退出。
)
echo.
pause
exit /b 0
