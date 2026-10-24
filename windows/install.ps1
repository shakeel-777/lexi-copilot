# ==============================================================================
#  ⚡ Lexi Dynamic Island & ShareMe Copilot — 1-Command Windows Installer
# ==============================================================================
# Usage in PowerShell:
#   irm https://raw.githubusercontent.com/shakeel-777/lexi-copilot/master/windows/install.ps1 | iex
# ==============================================================================

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "     ⚡ LEXI DYNAMIC ISLAND COPILOT FOR WINDOWS        " -ForegroundColor Green
Write-Host "   DeepSeek-V4.1-Flash · Groq LPU · ShareMe P2P Drop    " -ForegroundColor Gray
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Verify or Install Python 3.10+
Write-Host "[1/5] Checking Python installation..." -ForegroundColor Yellow
$pythonExe = $null

if (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonExe = (Get-Command python).Source
    Write-Host "      ✓ Found Python: $pythonExe" -ForegroundColor Green
} else {
    Write-Host "      [!] Python not found. Installing Python 3.11 via winget..." -ForegroundColor Cyan
    try {
        winget install Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
        # Refresh PATH
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
        $pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
    } catch {
        Write-Host "      [!] Automated winget install failed. Please install Python from https://www.python.org/" -ForegroundColor Red
        exit 1
    }
}

# 2. Setup Application Directory in %LOCALAPPDATA%\Lexi
$installDir = "$env:LOCALAPPDATA\Lexi"
Write-Host "[2/5] Setting up Lexi directory at $installDir..." -ForegroundColor Yellow
if (!(Test-Path $installDir)) {
    New-Item -ItemType Directory -Path $installDir -Force | Out-Null
}

# 3. Download Source Files from GitHub
Write-Host "[3/5] Downloading latest Lexi Windows modules..." -ForegroundColor Yellow
$baseUrl = "https://raw.githubusercontent.com/shakeel-777/lexi-copilot/master/windows"
$files = @("lexi_win_island.py", "lexi_win_actions.py", "lexi_win_drop.py", "requirements-windows.txt")

foreach ($file in $files) {
    $dest = Join-Path $installDir $file
    Write-Host "      Downloading $file..." -ForegroundColor Gray
    Invoke-WebRequest -Uri "$baseUrl/$file" -OutFile $dest -UseBasicParsing
}

# Download mascot avatar asset (with resilient fallback)
try {
    $avatarDest = Join-Path $installDir "shakeel_chibi.png"
    Invoke-WebRequest -Uri "$baseUrl/shakeel_chibi.png" -OutFile $avatarDest -UseBasicParsing -ErrorAction SilentlyContinue
} catch {}

# 4. Setup Virtual Environment & Dependencies
Write-Host "[4/5] Installing Python dependencies (PySide6, aiohttp, Pillow)..." -ForegroundColor Yellow
$venvDir = Join-Path $installDir ".venv"
if (!(Test-Path $venvDir)) {
    & python -m venv $venvDir
}

$venvPip = Join-Path $venvDir "Scripts\pip.exe"
$venvPythonW = Join-Path $venvDir "Scripts\pythonw.exe"

& $venvPip install --upgrade pip --quiet
& $venvPip install -r (Join-Path $installDir "requirements-windows.txt") --quiet
Write-Host "      ✓ Dependencies installed." -ForegroundColor Green

# 4b. Check or Install scrcpy for Android Phone Mirroring
Write-Host "      Checking Android phone mirroring engine (scrcpy)..." -ForegroundColor Yellow
if (!(Get-Command scrcpy -ErrorAction SilentlyContinue) -and !(Test-Path "$env:LOCALAPPDATA\Programs\scrcpy\scrcpy.exe") -and !(Test-Path "C:\scrcpy\scrcpy.exe")) {
    Write-Host "      [!] scrcpy not detected. Installing via winget for 60 FPS phone mirroring..." -ForegroundColor Cyan
    try {
        winget install Genymobile.scrcpy --silent --accept-package-agreements --accept-source-agreements
        Write-Host "      ✓ scrcpy installed successfully!" -ForegroundColor Green
    } catch {
        Write-Host "      (Optional) You can install scrcpy later: winget install Genymobile.scrcpy" -ForegroundColor Gray
    }
} else {
    Write-Host "      ✓ Found scrcpy phone mirror engine." -ForegroundColor Green
}

# 5. Configuration & API Key Setup
$configPath = "$env:USERPROFILE\.lexi_config.json"
if (!(Test-Path $configPath)) {
    Write-Host ""
    Write-Host "--------------------------------------------------------" -ForegroundColor Cyan
    Write-Host "  🔑 Configure AI Provider for Lexi" -ForegroundColor Yellow
    Write-Host "     1. DeepSeek Direct (V4.1-Flash Vision)" -ForegroundColor Gray
    Write-Host "     2. Groq (Ultra-Fast 500+ T/s · Llama 3.3)" -ForegroundColor Gray
    Write-Host "     3. OpenRouter (100+ Models)" -ForegroundColor Gray
    Write-Host "--------------------------------------------------------" -ForegroundColor Cyan
    
    $choice = Read-Host "Choose provider [1=DeepSeek, 2=Groq, 3=OpenRouter, Enter to skip]"
    $prov = "deepseek"
    $base = "https://api.deepseek.com"
    $model = "deepseek-flash"
    $apiKey = ""

    if ($choice -eq "2") {
        $prov = "groq"
        $base = "https://api.groq.com/openai/v1"
        $model = "llama-3.3-70b-versatile"
        $apiKey = Read-Host "Enter your Groq API Key (gsk_...)"
    } elseif ($choice -eq "3") {
        $prov = "openrouter"
        $base = "https://openrouter.ai/api/v1"
        $model = "deepseek/deepseek-v4.1-flash"
        $apiKey = Read-Host "Enter your OpenRouter API Key (sk-or-...)"
    } else {
        $apiKey = Read-Host "Enter your DeepSeek API Key (sk-...)"
    }

    $cfg = @{
        provider = $prov
        api_base = $base
        model = $model
        api_key = $apiKey.Trim()
        groq_api_base = "https://api.groq.com/openai/v1"
        groq_api_key = if ($prov -eq "groq") { $apiKey.Trim() } else { "" }
        groq_model = "llama-3.3-70b-versatile"
        deepseek_api_base = "https://api.deepseek.com"
        deepseek_api_key = if ($prov -eq "deepseek") { $apiKey.Trim() } else { "" }
        deepseek_model = "deepseek-flash"
        max_tokens = 4096
        system_prompt = "You are Lexi, an intelligent autonomous desktop AI companion on Windows created by Shakeel. Keep answers concise, direct, and conversational."
    } | ConvertTo-Json -Depth 4

    Set-Content -Path $configPath -Value $cfg -Encoding UTF8
    Write-Host "      ✓ Configuration saved to $configPath" -ForegroundColor Green
}

# 6. Create Desktop & Startup Shortcuts
Write-Host "[5/5] Creating Desktop & Startup shortcuts..." -ForegroundColor Yellow
$wsh = New-Object -ComObject WScript.Shell

# Desktop shortcut
$desktopLnk = Join-Path ([System.Environment]::GetFolderPath("Desktop")) "Lexi Copilot.lnk"
$sc = $wsh.CreateShortcut($desktopLnk)
$sc.TargetPath = $venvPythonW
$sc.Arguments = "`"$installDir\lexi_win_island.py`""
$sc.WorkingDirectory = $installDir
$sc.Description = "Lexi Dynamic Island Copilot"
$sc.Save()

# Windows Startup shortcut (Auto-start on boot)
$startupFolder = [System.Environment]::GetFolderPath("Startup")
$startupLnk = Join-Path $startupFolder "Lexi Copilot.lnk"
$sc2 = $wsh.CreateShortcut($startupLnk)
$sc2.TargetPath = $venvPythonW
$sc2.Arguments = "`"$installDir\lexi_win_island.py`""
$sc2.WorkingDirectory = $installDir
$sc2.Description = "Lexi Dynamic Island Copilot"
$sc2.Save()

Write-Host "      ✓ Shortcuts created on Desktop and Windows Startup." -ForegroundColor Green

# 7. Launch Lexi Dynamic Island Now
Write-Host ""
Write-Host "========================================================" -ForegroundColor Green
Write-Host "  ✓ INSTALLATION COMPLETE! Launching Lexi Island...     " -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green
Write-Host ""

Start-Process $venvPythonW -ArgumentList "`"$installDir\lexi_win_island.py`""
Write-Host "Lexi is now live in your top-screen notch! Enjoy." -ForegroundColor Cyan
