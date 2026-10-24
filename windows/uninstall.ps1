# ==============================================================================
# 🗑️ Lexi Dynamic Island Copilot — 1-Command Windows Uninstaller
# Completely stops and removes Lexi Copilot, desktop shortcuts, startup entries,
# and runtime files from your Windows PC.
# ==============================================================================
# Usage in PowerShell:
#   irm https://raw.githubusercontent.com/shakeel-777/lexi-copilot/master/windows/uninstall.ps1 | iex
# ==============================================================================

$ErrorActionPreference = "SilentlyContinue"

Write-Host ""
Write-Host "========================================================" -ForegroundColor Red
Write-Host "     🗑️  UNINSTALLING LEXI COPILOT FOR WINDOWS         " -ForegroundColor Yellow
Write-Host "========================================================" -ForegroundColor Red
Write-Host ""

# 1. Terminate running Lexi processes
Write-Host "[1/4] Stopping active Lexi processes..." -ForegroundColor Cyan
try {
    Get-CimInstance Win32_Process | Where-Object { 
        $_.CommandLine -like "*lexi_win_island*" -or $_.CommandLine -like "*\Lexi\*"
    } | ForEach-Object {
        Write-Host "      Stopping PID $($_.ProcessId) ($($_.Name))..." -ForegroundColor Gray
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
} catch {
    Stop-Process -Name "pythonw" -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1
Write-Host "      ✓ Active Lexi processes stopped." -ForegroundColor Green

# 2. Remove Desktop and Startup shortcuts
Write-Host "[2/4] Removing Desktop and Startup shortcuts..." -ForegroundColor Cyan
$desktopLnk = Join-Path ([System.Environment]::GetFolderPath("Desktop")) "Lexi Copilot.lnk"
if (Test-Path $desktopLnk) {
    Remove-Item -Path $desktopLnk -Force -ErrorAction SilentlyContinue
    Write-Host "      ✓ Removed Desktop shortcut." -ForegroundColor Green
}

$startupFolder = [System.Environment]::GetFolderPath("Startup")
$startupLnk = Join-Path $startupFolder "Lexi Copilot.lnk"
if (Test-Path $startupLnk) {
    Remove-Item -Path $startupLnk -Force -ErrorAction SilentlyContinue
    Write-Host "      ✓ Removed Windows Startup auto-launch entry." -ForegroundColor Green
}

# 3. Remove Installation & Virtual Environment Directory
$installDir = "$env:LOCALAPPDATA\Lexi"
Write-Host "[3/4] Removing Lexi application directory ($installDir)..." -ForegroundColor Cyan
if (Test-Path $installDir) {
    Remove-Item -Path $installDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "      ✓ Application directory and runtime removed." -ForegroundColor Green
} else {
    Write-Host "      ✓ Application directory was already clean." -ForegroundColor Gray
}

# 4. Optional: Remove Configuration & API Keys
$configPath = "$env:USERPROFILE\.lexi_config.json"
Write-Host "[4/4] Checking configuration and API keys..." -ForegroundColor Cyan
if (Test-Path $configPath) {
    $purge = $false
    if ($args -contains "--purge" -or $args -contains "-y") {
        $purge = $true
    } else {
        $choice = Read-Host "Do you want to delete your stored API keys & settings? (y/N)"
        if ($choice -match "^[Yy]") {
            $purge = $true
        }
    }

    if ($purge) {
        Remove-Item -Path $configPath -Force -ErrorAction SilentlyContinue
        Write-Host "      ✓ Removed configuration file ($configPath)." -ForegroundColor Green
    } else {
        Write-Host "      ℹ Preserved configuration file ($configPath)." -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "========================================================" -ForegroundColor Green
Write-Host "  ✓ UNINSTALL COMPLETE! Lexi has been removed cleanly.  " -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green
Write-Host ""
