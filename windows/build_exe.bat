@echo off
title Build Lexi.exe for Windows

echo ========================================================
echo        📦 Compiling Lexi.exe Standalone Executable
echo ========================================================
echo.

if not exist "%~dp0.venv" (
    echo Virtual environment missing. Running setup_windows.bat first...
    call "%~dp0setup_windows.bat"
)

call "%~dp0.venv\Scripts\activate.bat"
echo [*] Ensuring PyInstaller build tools are installed...
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements-build.txt" --quiet
echo [*] Bundling Lexi into standalone executable...
"%~dp0.venv\Scripts\python.exe" -m PyInstaller --noconsole --onefile --clean ^
    --name "Lexi" ^
    --icon "%~dp0lexi.ico" ^
    --add-data "%~dp0lexi_win_actions.py;." ^
    --add-data "%~dp0lexi_win_drop.py;." ^
    --add-data "%~dp0lexi.ico;." ^
    --add-data "%~dp0lexi_icon.png;." ^
    "%~dp0lexi_win_island.py"

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ========================================================
    echo [✓] SUCCESS! Your executable is ready at:
    echo     %~dp0dist\Lexi.exe
    echo ========================================================
    explorer.exe "%~dp0dist"
) else (
    echo [!] Build failed. Check console output above.
)
pause
