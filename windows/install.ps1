# ==============================================================================
#  [+] Lexi Dynamic Island & ShareMe Copilot - 1-Command Windows Installer
# ==============================================================================
# Usage in PowerShell:
#   irm https://raw.githubusercontent.com/shakeel-777/lexi-copilot/master/windows/install.ps1 | iex
# ==============================================================================

[CmdletBinding()]
param(
    [string]$ApiKey = "",
    [string]$Provider = "",
    [string]$Model = "",
    [switch]$Unattended,
    [switch]$Force,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

Write-Host ""
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "     [+] LEXI DYNAMIC ISLAND COPILOT FOR WINDOWS        " -ForegroundColor Green
Write-Host "   DeepSeek-V4.1-Flash | Groq LPU | ShareMe P2P Drop    " -ForegroundColor Gray
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Verify or Install Python 3.10+
Write-Host "[1/5] Checking Python installation..." -ForegroundColor Yellow
$pyCmd = $null

foreach ($candidate in @("python", "py", "python3")) {
    try {
        $testOut = (& $candidate --version 2>&1) | Out-String
        if ($testOut -match "Python 3\.") {
            $pyCmd = $candidate
            Write-Host "      [OK] Found: $testOut".Trim() -ForegroundColor Green
            break
        }
    } catch {
        $null = $_
    }
}

# If still null, search common Windows Python standard installation paths
if ($null -eq $pyCmd) {
    $searchPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python310\python.exe",
        "$env:ProgramFiles\Python311\python.exe"
    )
    foreach ($sp in $searchPaths) {
        if (Test-Path $sp) {
            $pyCmd = $sp
            Write-Host "      [OK] Found Python at $sp" -ForegroundColor Green
            break
        }
    }
}

if ($null -eq $pyCmd) {
    Write-Host "      [!] Python 3 not detected. Attempting installation..." -ForegroundColor Cyan
    $installed = $false
    try {
        winget install Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
        $installed = $true
    } catch {
        $null = $_
    }

    if (-not $installed) {
        Write-Host "      [*] Downloading official Python 3.11 installer from python.org..." -ForegroundColor Cyan
        try {
            $pyInstaller = "$env:TEMP\python-3.11-installer.exe"
            Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" -OutFile $pyInstaller -UseBasicParsing
            Write-Host "      [*] Running Python 3.11 silent install..." -ForegroundColor Gray
            Start-Process -FilePath $pyInstaller -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0" -Wait
            Remove-Item $pyInstaller -Force -ErrorAction SilentlyContinue
            $installed = $true
        } catch {
            Write-Host "      [!] Automated installer failed. Please install Python 3.10+ from https://www.python.org/" -ForegroundColor Red
            exit 1
        }
    }

    # Refresh PATH and detect Python
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    foreach ($candidate in @("python", "py", "python3")) {
        try {
            $testOut = (& $candidate --version 2>&1) | Out-String
            if ($testOut -match "Python 3\.") { $pyCmd = $candidate; break }
        } catch {
            $null = $_
        }
    }
    if ($null -eq $pyCmd) {
        if (Test-Path "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe") {
            $pyCmd = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
        }
    }
}

# 2. Setup Application Directory in %LOCALAPPDATA%\Lexi
$installDir = "$env:LOCALAPPDATA\Lexi"
Write-Host "[2/5] Setting up Lexi directory at $installDir..." -ForegroundColor Yellow
if (!(Test-Path $installDir)) {
    New-Item -ItemType Directory -Path $installDir -Force | Out-Null
}

# 3. Obtain Source Files (Local Copy or GitHub Download)
Write-Host "[3/5] Syncing latest Lexi Windows modules..." -ForegroundColor Yellow
$files = @("lexi_win_island.py", "lexi_win_actions.py", "lexi_win_drop.py", "requirements-windows.txt")
$isLocal = ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "lexi_win_island.py")))

if ($isLocal) {
    Write-Host "      Using local source files from $PSScriptRoot..." -ForegroundColor Green
    foreach ($file in $files) {
        Copy-Item (Join-Path $PSScriptRoot $file) (Join-Path $installDir $file) -Force
    }
    if (Test-Path (Join-Path $PSScriptRoot "lexi_icon.png")) {
        Copy-Item (Join-Path $PSScriptRoot "lexi_icon.png") (Join-Path $installDir "lexi_icon.png") -Force
    }
    if (Test-Path (Join-Path $PSScriptRoot "lexi.ico")) {
        Copy-Item (Join-Path $PSScriptRoot "lexi.ico") (Join-Path $installDir "lexi.ico") -Force
    }
} else {
    Write-Host "      Downloading latest modules from GitHub repository..." -ForegroundColor Gray
    $baseUrl = "https://raw.githubusercontent.com/shakeel-777/lexi-copilot/master/windows"
    foreach ($file in $files) {
        $dest = Join-Path $installDir $file
        Write-Host "      Downloading $file..." -ForegroundColor Gray
        Invoke-WebRequest -Uri "$baseUrl/$file" -OutFile $dest -UseBasicParsing
    }
    try {
        $iconDest = Join-Path $installDir "lexi_icon.png"
        Invoke-WebRequest -Uri "$baseUrl/lexi_icon.png" -OutFile $iconDest -UseBasicParsing -ErrorAction SilentlyContinue
        $icoDest = Join-Path $installDir "lexi.ico"
        Invoke-WebRequest -Uri "$baseUrl/lexi.ico" -OutFile $icoDest -UseBasicParsing -ErrorAction SilentlyContinue
    } catch {
        $null = $_
    }
}

# 4. Setup Virtual Environment & Dependencies
Write-Host "[4/5] Installing Python dependencies (PySide6, aiohttp, Pillow, psutil)..." -ForegroundColor Yellow
$venvDir = Join-Path $installDir ".venv"
if (!(Test-Path $venvDir)) {
    Write-Host "      Creating isolated virtual environment..." -ForegroundColor Gray
    if ($pyCmd -eq "py") {
        py -3 -m venv $venvDir
    } else {
        & $pyCmd -m venv $venvDir
    }
}

$venvPython = Join-Path $venvDir "Scripts\python.exe"
$venvPythonW = Join-Path $venvDir "Scripts\pythonw.exe"

$cacheDir = Join-Path $installDir ".cache"
New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null
& $venvPython -m pip install --upgrade pip --cache-dir $cacheDir --quiet
& $venvPython -m pip install -r (Join-Path $installDir "requirements-windows.txt") --cache-dir $cacheDir --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "      [!] Pip install warning/retry with verbose output..." -ForegroundColor Yellow
    & $venvPython -m pip install -r (Join-Path $installDir "requirements-windows.txt") --cache-dir $cacheDir
}
Write-Host "      [OK] Dependencies installed." -ForegroundColor Green

# 4b. Check or Install scrcpy for Android Phone Mirroring
Write-Host "      Checking Android phone mirroring engine (scrcpy)..." -ForegroundColor Yellow
if (!(Get-Command scrcpy -ErrorAction SilentlyContinue) -and !(Test-Path "$env:LOCALAPPDATA\Programs\scrcpy\scrcpy.exe") -and !(Test-Path "C:\scrcpy\scrcpy.exe")) {
    Write-Host "      [!] scrcpy not detected. Installing via winget for 60 FPS phone mirroring..." -ForegroundColor Cyan
    try {
        winget install Genymobile.scrcpy --silent --accept-package-agreements --accept-source-agreements
        Write-Host "      [OK] scrcpy installed successfully!" -ForegroundColor Green
    } catch {
        Write-Host "      (Optional) You can install scrcpy later: winget install Genymobile.scrcpy" -ForegroundColor Gray
    }
} else {
    Write-Host "      [OK] Found scrcpy phone mirror engine." -ForegroundColor Green
}

# 4c. Setup Windows Firewall Rules for ShareMe Beam LAN P2P
Write-Host "      Configuring Windows Firewall rules for ShareMe Beam (Ports 7890, 7891)..." -ForegroundColor Yellow
try {
    New-NetFirewallRule -DisplayName "Lexi Beam TCP (Port 7890)" -Direction Inbound -LocalPort 7890 -Protocol TCP -Action Allow -ErrorAction SilentlyContinue | Out-Null
    New-NetFirewallRule -DisplayName "Lexi Beam UDP Beacon (Port 7891)" -Direction Inbound -LocalPort 7891 -Protocol UDP -Action Allow -ErrorAction SilentlyContinue | Out-Null
    Write-Host "      [OK] Firewall rules configured for local LAN zero-data drop." -ForegroundColor Green
} catch {
    $null = $_
}

# 5. Configuration & API Key Setup
$configPath = "$env:USERPROFILE\.lexi_config.json"

# Check if environment is interactive (protect against hanging on piped iex)
$isInteractive = $true
try {
    if ([Console]::IsInputRedirected) { $isInteractive = $false }
} catch {
    $isInteractive = $false
}
if ($Unattended) { $isInteractive = $false }

# Resolve API key from arguments or environment variables
if ([string]::IsNullOrWhiteSpace($ApiKey)) {
    if ($env:GROQ_API_KEY) {
        $ApiKey = $env:GROQ_API_KEY
        if (!$Provider) { $Provider = "groq" }
    } elseif ($env:LEXI_API_KEY) {
        $ApiKey = $env:LEXI_API_KEY
    }
}

$prov = if ($Provider) { $Provider.ToLower().Trim() } else { "deepseek" }
$base = "https://api.deepseek.com"
$model = if ($Model) { $Model.Trim() } else { "deepseek-flash" }

# Auto-detect provider from API key signature
if ($ApiKey.Trim().StartsWith("gsk_")) {
    $prov = "groq"
    $base = "https://api.groq.com/openai/v1"
    $model = if ($Model) { $Model.Trim() } else { "llama-3.3-70b-versatile" }
    Write-Host "      [i] Auto-detected Groq API key: provider set to Groq ($model)" -ForegroundColor Green
} elseif ($ApiKey.Trim().StartsWith("sk-or-")) {
    $prov = "openrouter"
    $base = "https://openrouter.ai/api/v1"
    $model = if ($Model) { $Model.Trim() } else { "deepseek/deepseek-v4.1-flash" }
} elseif ($ApiKey.Trim().StartsWith("sk-proj-")) {
    $prov = "openai"
    $base = "https://api.openai.com/v1"
    $model = if ($Model) { $Model.Trim() } else { "gpt-4o-mini" }
}

if (Test-Path $configPath) {
    if ($ApiKey -or $Provider -or $Force) {
        Write-Host "      Updating existing configuration with provided parameters..." -ForegroundColor Cyan
        try {
            $cfgObj = Get-Content $configPath -Raw | ConvertFrom-Json
        } catch {
            $cfgObj = [PSCustomObject]@{}
        }
        $cfgObj.provider = $prov
        if ($ApiKey) { $cfgObj.api_key = $ApiKey.Trim() }
        if ($prov -eq "groq") {
            $cfgObj.groq_api_base = "https://api.groq.com/openai/v1"
            $cfgObj.groq_model = $model
            if ($ApiKey) { $cfgObj.groq_api_key = $ApiKey.Trim() }
        }
        $cfgObj | ConvertTo-Json -Depth 4 | Set-Content -Path $configPath -Encoding UTF8
        Write-Host "      [OK] Updated $configPath successfully." -ForegroundColor Green
    } else {
        Write-Host "      [OK] Preserving existing configuration ($configPath)." -ForegroundColor Green
    }
} else {
    # No configuration exists yet
    if ($isInteractive -and -not $ApiKey) {
        Write-Host ""
        Write-Host "--------------------------------------------------------" -ForegroundColor Cyan
        Write-Host "  [*] Configure AI Provider for Lexi" -ForegroundColor Yellow
        Write-Host "     1. DeepSeek Direct (V4.1-Flash Vision)" -ForegroundColor Gray
        Write-Host "     2. Groq (Ultra-Fast 500+ T/s | Llama 3.3)" -ForegroundColor Gray
        Write-Host "     3. OpenRouter (100+ Models)" -ForegroundColor Gray
        Write-Host "--------------------------------------------------------" -ForegroundColor Cyan

        $choice = Read-Host "Choose provider [1=DeepSeek, 2=Groq, 3=OpenRouter, Enter to skip]"
        if ($choice -eq "2") {
            $prov = "groq"
            $base = "https://api.groq.com/openai/v1"
            $model = "llama-3.3-70b-versatile"
            $ApiKey = Read-Host "Enter your Groq API Key (gsk_...)"
        } elseif ($choice -eq "3") {
            $prov = "openrouter"
            $base = "https://openrouter.ai/api/v1"
            $model = "deepseek/deepseek-v4.1-flash"
            $ApiKey = Read-Host "Enter your OpenRouter API Key (sk-or-...)"
        } else {
            $ApiKey = Read-Host "Enter your DeepSeek API Key (sk-...)"
        }

        # Check again if user pasted a Groq key into choice 1 or 3
        if ($ApiKey.Trim().StartsWith("gsk_")) {
            $prov = "groq"
            $base = "https://api.groq.com/openai/v1"
            $model = "llama-3.3-70b-versatile"
            Write-Host "      [i] Auto-detected Groq key: setting provider to Groq (Llama 3.3 70B)" -ForegroundColor Green
        }
    }

    $groqKey = if ($prov -eq "groq") { $ApiKey.Trim() } else { "" }
    $deepseekKey = if ($prov -eq "deepseek") { $ApiKey.Trim() } else { "" }
    $openrouterKey = if ($prov -eq "openrouter") { $ApiKey.Trim() } else { "" }

    $cfg = @{
        provider = $prov
        api_base = $base
        model = $model
        api_key = $ApiKey.Trim()
        groq_api_base = "https://api.groq.com/openai/v1"
        groq_api_key = $groqKey
        groq_model = "llama-3.3-70b-versatile"
        deepseek_api_base = "https://api.deepseek.com"
        deepseek_api_key = $deepseekKey
        deepseek_model = "deepseek-flash"
        openrouter_api_base = "https://openrouter.ai/api/v1"
        openrouter_api_key = $openrouterKey
        openrouter_model = "deepseek/deepseek-v4.1-flash"
        max_tokens = 4096
        system_prompt = "You are Lexi, an intelligent autonomous desktop AI companion on Windows created by Shakeel. Keep answers concise, direct, and conversational."
    } | ConvertTo-Json -Depth 4

    Set-Content -Path $configPath -Value $cfg -Encoding UTF8
    Write-Host "      [OK] Configuration saved to $configPath" -ForegroundColor Green
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
if (Test-Path "$installDir\lexi.ico") {
    $sc.IconLocation = "$installDir\lexi.ico,0"
}
$sc.Save()

# Windows Startup shortcut (Auto-start on boot)
$startupFolder = [System.Environment]::GetFolderPath("Startup")
$startupLnk = Join-Path $startupFolder "Lexi Copilot.lnk"
$sc2 = $wsh.CreateShortcut($startupLnk)
$sc2.TargetPath = $venvPythonW
$sc2.Arguments = "`"$installDir\lexi_win_island.py`""
$sc2.WorkingDirectory = $installDir
$sc2.Description = "Lexi Dynamic Island Copilot"
if (Test-Path "$installDir\lexi.ico") {
    $sc2.IconLocation = "$installDir\lexi.ico,0"
}
$sc2.Save()

Write-Host "      [OK] Shortcuts created on Desktop and Windows Startup." -ForegroundColor Green

# 7. Launch Lexi Dynamic Island Now
Write-Host ""
Write-Host "========================================================" -ForegroundColor Green
Write-Host "  [OK] INSTALLATION COMPLETE! Launching Lexi Island...     " -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green
Write-Host ""

if (-not $NoLaunch) {
    Start-Process $venvPythonW -ArgumentList "`"$installDir\lexi_win_island.py`""
    Write-Host "Lexi is now live in your top-screen notch! Enjoy." -ForegroundColor Cyan
} else {
    Write-Host "Installation complete. (Skipped auto-launch due to -NoLaunch flag)" -ForegroundColor Cyan
}
