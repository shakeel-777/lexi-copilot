@echo off
setlocal EnableDelayedExpansion
title Lexi Windows Setup & Launcher

echo ========================================================
echo        ⚡ LEXI DYNAMIC ISLAND COPILOT FOR WINDOWS
echo ========================================================
echo.

:: 1. Check Python
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python 3 was not found in your system PATH.
    echo Please install Python 3.10+ from https://www.python.org/ or the Microsoft Store.
    echo (Make sure to check "Add Python to PATH" during installation)
    pause
    exit /b 1
)

:: 2. Create virtual environment
if not exist "%~dp0.venv" (
    echo [*] Creating isolated virtual environment...
    python -m venv "%~dp0.venv"
)

:: 3. Install requirements
echo [*] Installing Windows dependencies (PySide6, aiohttp, Pillow)...
call "%~dp0.venv\Scripts\activate.bat"
pip install --upgrade pip >nul 2>&1
pip install -r "%~dp0requirements-windows.txt"

:: 4. Prompt for API key if config does not exist
set "CONFIG_PATH=%USERPROFILE%\.lexi_config.json"
if not exist "%CONFIG_PATH%" (
    echo.
    echo --------------------------------------------------------
    echo [*] DeepSeek AI Configuration
    echo --------------------------------------------------------
    set /p "DS_KEY=Enter your DeepSeek API Key (press Enter to skip): "
    
    (
        echo {
        echo   "provider": "deepseek",
        echo   "api_base": "https://api.deepseek.com",
        echo   "api_key": "!DS_KEY!",
        echo   "model": "deepseek-flash",
        echo   "max_tokens": 4096
        echo }
    ) > "%CONFIG_PATH%"
    echo [✓] Configuration saved to %CONFIG_PATH%
)

:: 5. Launch Lexi Dynamic Island
echo.
echo [✓] Launching Lexi Dynamic Island on Windows...
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0lexi_win_island.py"
echo Lexi is now running in the top-screen notch!
exit /b 0
