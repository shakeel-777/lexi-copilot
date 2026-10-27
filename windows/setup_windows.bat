@echo off
setlocal EnableDelayedExpansion
title Lexi Windows Setup & Launcher

echo ========================================================
echo        * LEXI DYNAMIC ISLAND COPILOT FOR WINDOWS
echo ========================================================
echo.

:: 1. Search for genuine Python 3
set "PY_EXE="

:: Check py launcher
py -3 --version >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "PY_EXE=py -3"
    goto :python_found
)

:: Check python command directly (verify not Microsoft Store dummy stub)
for /f "tokens=*" %%i in ('python --version 2^>^&1') do (
    set "VER_CHECK=%%i"
    echo !VER_CHECK! | findstr /i "Python 3." >nul
    if !ERRORLEVEL! EQU 0 (
        set "PY_EXE=python"
        goto :python_found
    )
)

:: Search known standard Windows installation directories
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    goto :python_found
)
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    goto :python_found
)
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" (
    set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    goto :python_found
)
if exist "C:\Python312\python.exe" (
    set "PY_EXE=C:\Python312\python.exe"
    goto :python_found
)
if exist "C:\Python311\python.exe" (
    set "PY_EXE=C:\Python311\python.exe"
    goto :python_found
)
if exist "C:\Python310\python.exe" (
    set "PY_EXE=C:\Python310\python.exe"
    goto :python_found
)
if exist "%ProgramFiles%\Python311\python.exe" (
    set "PY_EXE=%ProgramFiles%\Python311\python.exe"
    goto :python_found
)

:: If not found, attempt automated install via winget or curl
echo [!] Python 3 not detected. Attempting automatic installation...
winget install Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
        set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        goto :python_found
    )
)

echo [!] Automated installation via winget was unavailable.
echo [*] Downloading official Python 3.11 installer from python.org...
curl -sSL -o "%TEMP%\python_installer.exe" "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
if exist "%TEMP%\python_installer.exe" (
    echo [*] Running Python 3.11 silent installer...
    "%TEMP%\python_installer.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
    del "%TEMP%\python_installer.exe" >nul 2>&1
    if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
        set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        goto :python_found
    )
)

echo [ERROR] Could not automatically install Python 3.
echo Please install Python 3.10+ manually from https://www.python.org/
echo Check the box "Add Python to PATH" during installation.
pause
exit /b 1

:python_found
echo [OK] Using Python: !PY_EXE!

:: 2. Create virtual environment
if not exist "%~dp0.venv" (
    echo [*] Creating isolated virtual environment...
    !PY_EXE! -m venv "%~dp0.venv"
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

:: 3. Install requirements
echo [*] Installing Windows dependencies (PySide6, aiohttp, Pillow, psutil)...
"%~dp0.venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements-windows.txt" --quiet
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to install requirements.
    pause
    exit /b 1
)
echo [OK] Dependencies installed successfully.

:: 4. Prompt for API key if config does not exist
set "CONFIG_PATH=%USERPROFILE%\.lexi_config.json"
if not exist "%CONFIG_PATH%" (
    echo.
    echo --------------------------------------------------------
    echo [*] AI Provider Configuration
    echo --------------------------------------------------------
    echo Choose your AI provider:
    echo   1. DeepSeek (Default - High Speed V4.1-Flash)
    echo   2. Groq (Ultra-Fast 500+ T/s - Llama 3.3 70B)
    echo   3. OpenRouter (Multi-Model Hub)
    echo.
    set /p "CHOICE=Enter choice [1, 2, or 3] (press Enter for DeepSeek): "
    
    set "PROV=deepseek"
    set "API_BASE=https://api.deepseek.com"
    set "MODEL=deepseek-flash"
    
    if "!CHOICE!"=="2" (
        set "PROV=groq"
        set "API_BASE=https://api.groq.com/openai/v1"
        set "MODEL=llama-3.3-70b-versatile"
        set /p "USER_KEY=Enter your Groq API Key (gsk_...): "
    ) else if "!CHOICE!"=="3" (
        set "PROV=openrouter"
        set "API_BASE=https://openrouter.ai/api/v1"
        set "MODEL=deepseek/deepseek-v4.1-flash"
        set /p "USER_KEY=Enter your OpenRouter API Key (sk-or-...): "
    ) else (
        set /p "USER_KEY=Enter your DeepSeek API Key (sk-...): "
    )
    
    if defined USER_KEY (
        set "KEY_PREFIX=!USER_KEY:~0,4!"
        if /i "!KEY_PREFIX!"=="gsk_" (
            set "PROV=groq"
            set "API_BASE=https://api.groq.com/openai/v1"
            set "MODEL=llama-3.3-70b-versatile"
            echo [i] Auto-detected Groq key: provider set to Groq (Llama 3.3 70B)
        )
    )
    
    (
        echo {
        echo   "provider": "!PROV!",
        echo   "api_base": "!API_BASE!",
        echo   "api_key": "!USER_KEY!",
        echo   "model": "!MODEL!",
        echo   "groq_api_base": "https://api.groq.com/openai/v1",
        echo   "groq_api_key": "!USER_KEY!",
        echo   "groq_model": "llama-3.3-70b-versatile",
        echo   "deepseek_api_base": "https://api.deepseek.com",
        echo   "deepseek_api_key": "!USER_KEY!",
        echo   "deepseek_model": "deepseek-flash",
        echo   "openrouter_api_base": "https://openrouter.ai/api/v1",
        echo   "openrouter_api_key": "!USER_KEY!",
        echo   "openrouter_model": "deepseek/deepseek-v4.1-flash",
        echo   "max_tokens": 4096,
        echo   "system_prompt": "You are Lexi, an intelligent autonomous desktop AI companion on Windows created by Shakeel. Keep answers concise, direct, and conversational."
        echo }
    ) > "%CONFIG_PATH%"
    copy /y "%CONFIG_PATH%" "%~dp0config.json" >nul 2>&1
    if not exist "%LOCALAPPDATA%\Lexi" mkdir "%LOCALAPPDATA%\Lexi" >nul 2>&1
    copy /y "%CONFIG_PATH%" "%LOCALAPPDATA%\Lexi\.lexi_config.json" >nul 2>&1
    copy /y "%CONFIG_PATH%" "%LOCALAPPDATA%\Lexi\config.json" >nul 2>&1
    echo [OK] Configuration saved to %CONFIG_PATH% and %~dp0config.json
)

:: 5. Launch Lexi Dynamic Island
echo.
echo [OK] Launching Lexi Dynamic Island on Windows...
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0lexi_win_island.py"
echo Lexi is now live in your top-screen notch!
exit /b 0
