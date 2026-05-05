@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

cd /d "%~dp0"
title 利润助手 Windows 一键打包

set "PYTHON_CMD="
if exist "%SystemRoot%\py.exe" (
    set "PYTHON_CMD=%SystemRoot%\py.exe -3"
) else (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo [错误] 未检测到 Python 3。
    echo 请先安装 Python 3.11 或以上版本，并勾选“Add Python to PATH”。
    pause
    exit /b 1
)

set "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
set "PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn"
set "VENV_DIR=.venv-win"

echo [1/5] 创建虚拟环境...
if not exist "%VENV_DIR%\Scripts\python.exe" (
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败。
        pause
        exit /b 1
    )
)

call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 (
    echo [错误] 激活虚拟环境失败。
    pause
    exit /b 1
)

echo [2/5] 升级打包基础工具...
python -m pip install --upgrade pip setuptools wheel ^
    -i %PIP_INDEX_URL% --trusted-host %PIP_TRUSTED_HOST%
if errorlevel 1 (
    echo [错误] 升级 pip 失败。
    pause
    exit /b 1
)

echo [3/5] 安装项目依赖（清华镜像）...
pip install -r requirements.txt ^
    -i %PIP_INDEX_URL% --trusted-host %PIP_TRUSTED_HOST%
if errorlevel 1 (
    echo [错误] 安装 requirements 失败。
    pause
    exit /b 1
)

echo [4/5] 清理旧打包结果...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [5/5] 生成 Windows EXE...
pyinstaller --noconfirm --clean --windowed --onefile ^
    --name "利润助手" ^
    --icon "assets\app_icon.ico" ^
    --add-data "assets;assets" ^
    main.py
if errorlevel 1 (
    echo [错误] PyInstaller 打包失败。
    pause
    exit /b 1
)

if exist "dist\利润助手.exe" (
    echo.
    echo 打包成功：%cd%\dist\利润助手.exe
    echo 说明：首次运行会在 exe 同级目录自动生成 family_ledger.db。
) else (
    echo [错误] 未找到生成的 EXE。
    pause
    exit /b 1
)

pause
