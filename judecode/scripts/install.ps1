#Requires -Version 5.1
param(
    [switch]$Global,
    [string]$Python = "python"
)

<#
.SYNOPSIS
    Jude Code Installer for Windows
.DESCRIPTION
    Installs Jude Code on Windows with a single command.
    - Checks Python 3.10+
    - Installs dependencies
    - Installs judecode into PATH
    - Optional global (all users) install with -Global flag
.NOTES
    Run as Administrator if using -Global
    Run without args for user-only install (no admin needed)
#>

$ErrorActionPreference = "Stop"

function Write-Header($msg) { Write-Host "`n>>> $msg" -ForegroundColor Cyan }
function Write-Success($msg) { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Write-Err($msg) { Write-Host "  [ERR] $msg" -ForegroundColor Red }

# ─── 1. Check Python ───────────────────────────────────────────
Write-Header "Checking Python..."
try {
    $pyVersion = & $Python --version 2>&1
    if ($pyVersion -match "Python (\d+)\.(\d+)") {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
            Write-Err "Python 3.10+ required but found $major.$minor"
            Write-Host "`nPlease install Python 3.10 or newer from https://python.org/downloads/"
            Write-Host "Make sure to check 'Add Python to PATH' during installation."
            exit 1
        }
        Write-Success "Python $major.$minor found ($( (Get-Command $Python).Source ))"
    } else {
        throw "Cannot parse python version"
    }
} catch {
    Write-Err "Python not found. Please install Python 3.10+ from https://python.org/downloads/"
    Write-Host "Make sure to check 'Add Python to PATH' during installation."
    exit 1
}

# ─── 2. Check pip ────────────────────────────────────────────
Write-Header "Checking pip..."
try {
    & $Python -m pip --version | Out-Null
    Write-Success "pip available"
} catch {
    Write-Header "Installing pip..."
    $getPip = "$env:TEMP\get-pip.py"
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip -UseBasicParsing
    & $Python $getPip
    Remove-Item $getPip -ErrorAction SilentlyContinue
    Write-Success "pip installed"
}

# ─── 3. Check Git (optional but recommended) ──────────────────
Write-Header "Checking Git..."
try {
    $gitVersion = git --version 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Git found: $gitVersion"
    } else { throw }
} catch {
    Write-Warn "Git not found. Some features like cloning repos may not work."
    Write-Host "  Download: https://git-scm.com/download/win" -ForegroundColor DarkGray
}

# ─── 4. Upgrade pip, setuptools, wheel ────────────────────────
Write-Header "Upgrading build tools..."
& $Python -m pip install --upgrade pip setuptools wheel | Out-Null
Write-Success "Build tools upgraded"

# ─── 5. Determine install target ──────────────────────────────
$repoRoot = Split-Path -Parent $PSScriptRoot
$installTarget = if ($Global) {
    Write-Header "Installing for ALL USERS (requires Administrator)"
    "--system"
} else {
    Write-Header "Installing for current user only"
    "--user"
}

# ─── 6. Install dependencies ───────────────────────────────────
Write-Header "Installing dependencies..."
$reqFile = Join-Path $repoRoot "requirements.txt"
if (Test-Path $reqFile) {
    & $Python -m pip install $installTarget -r $reqFile --upgrade | Out-Null
}
Write-Success "Dependencies installed"

# ─── 7. Install judecode package ─────────────────────────────
Write-Header "Installing judecode package..."
Push-Location $repoRoot
try {
    & $Python -m pip install $installTarget -e . --upgrade
} finally {
    Pop-Location
}
Write-Success "judecode installed"

# ─── 8. Verify judecode is in PATH ────────────────────────────
Write-Header "Verifying installation..."

# Discover the real Scripts directories pip uses on this machine.
# pip may install into sysconfig scripts OR into user-base scripts
# (e.g.  C:\Users\xxx\AppData\Roaming\Python\Python313\Scripts).
$knownDirs = @()

# 1. sysconfig scripts
$d1 = & $Python -c "import sysconfig; print(sysconfig.get_path('scripts'))" 2>$null
if ($d1) { $knownDirs += $d1 }

# 2. user-base scripts (Roaming)
$d2 = & $Python -c "import site, sys, os; base=site.getusersitepackages(); print(os.path.join(os.path.dirname(base), 'Scripts'))" 2>$null
if ($d2) { $knownDirs += $d2 }

# 3. fallback next to python.exe
$pyBase = Split-Path (Get-Command $Python).Source -Parent
$knownDirs += (Join-Path $pyBase "Scripts")

# 4. pip show location -> derive Scripts
$pipShow = & $Python -m pip show judecode 2>$null | Select-String "Location:"
if ($pipShow) {
    $loc = ($pipShow.Line -replace "Location:\s*", "").Trim()
    # site-packages -> parent dir -> Scripts
    $parent = Split-Path $loc -Parent
    $knownDirs += (Join-Path $parent "Scripts")
}

# Find which directory actually contains judecode.exe
$scriptsDir = $null
foreach ($dir in ($knownDirs | Select-Object -Unique)) {
    if (Test-Path $dir) {
        $exe = Join-Path $dir "judecode.exe"
        if (Test-Path $exe) {
            $scriptsDir = $dir
            break
        }
    }
}
if (-not $scriptsDir) {
    # Last resort: take first known dir that exists
    $scriptsDir = ($knownDirs | Where-Object { Test-Path $_ } | Select-Object -First 1)
}
if (-not $scriptsDir) {
    $scriptsDir = $knownDirs[0]
}
Write-Success "Scripts directory: $scriptsDir"

# Force-reload current session PATH from registry + add scripts dir
$env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
if ($env:Path -notlike "*${scriptsDir}*") {
    $env:Path = "$env:Path;$scriptsDir"
}

# Also add to persistent PATH if missing
$target = if ($Global) { "Machine" } else { "User" }
$currentPath = [Environment]::GetEnvironmentVariable("Path", $target)
if ($currentPath -notlike "*${scriptsDir}*") {
    [Environment]::SetEnvironmentVariable("Path", "$currentPath;$scriptsDir", $target)
    Write-Success "Added Scripts directory to persistent PATH"
}

# Verify judecode works
$found = $false
try {
    $judecodeVersion = judecode --version 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Success "judecode command works!"
        $found = $true
    }
} catch {}

if (-not $found) {
    Write-Warn "judecode not found in PATH. Adding it now..."
    if ($env:Path -notlike "*${scriptsDir}*") {
        $env:Path = "$env:Path;$scriptsDir"
    }
    $target = if ($Global) { "Machine" } else { "User" }
    $currentPath = [Environment]::GetEnvironmentVariable("Path", $target)
    if ($currentPath -notlike "*${scriptsDir}*") {
        [Environment]::SetEnvironmentVariable("Path", "$currentPath;$scriptsDir", $target)
    }
    Write-Success "PATH updated"
} else {
    Write-Success "judecode is ready to use!"
}

# ─── 9. Final instructions ────────────────────────────────────
Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  Jude Code installed successfully!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "`nUsage:" -ForegroundColor White
Write-Host "  judecode        Start interactive session" -ForegroundColor Gray
Write-Host "  judecode --help Show help" -ForegroundColor Gray
Write-Host "`nNote:" -ForegroundColor Yellow
if (-not $Global) {
    Write-Host "  If 'judecode' command is not found, restart your terminal or run:" -ForegroundColor DarkGray
    Write-Host "  `$env:Path = [Environment]::GetEnvironmentVariable('Path', 'User')" -ForegroundColor DarkGray
}
