#Requires -Version 5.1
param(
    [switch]$Global,
    [switch]$Venv,
    [string]$Python = "python"
)

<#
.SYNOPSIS
    Jude Code Installer for Windows
.DESCRIPTION
    Installs Jude Code on Windows with a single command.
    - Checks Python 3.10+
    - Auto-detects PEP 668 (externally-managed) and creates venv
    - Installs dependencies
    - Installs judecode into PATH
    - Optional global (all users) install with -Global flag
    - Optional virtual environment with -Venv flag
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

# ─── 4. Detect if we should use venv ─────────────────────────
$repoRoot = Split-Path -Parent $PSScriptRoot
$useVenv = $Venv

if (-not $useVenv -and -not $Global) {
    # Check if Python is externally managed (PEP 668 on some Windows Python installs)
    try {
        $testResult = & $Python -m pip install --dry-run fakepkg 2>&1
        if ($testResult -match "externally-managed") {
            Write-Warn "Python appears to be externally managed."
            $useVenv = $true
        }
    } catch {
        # If dry-run fails, that's okay - likely not externally managed
    }
}

$venvDir = Join-Path $repoRoot ".venv"
if ($useVenv) {
    Write-Header "Setting up virtual environment..."
    if (-not (Test-Path $venvDir)) {
        & $Python -m venv $venvDir
        Write-Success "Virtual environment created: $venvDir"
    } else {
        Write-Success "Virtual environment already exists: $venvDir"
    }
    # Re-point to venv Python
    $Python = Join-Path $venvDir "Scripts\python.exe"
    if (-not (Test-Path $Python)) {
        Write-Err "Virtual environment Python not found at: $Python"
        exit 1
    }
    Write-Success "Using venv Python: $Python"
}

# ─── 5. Upgrade pip, setuptools, wheel ────────────────────────
Write-Header "Upgrading build tools..."
& $Python -m pip install --upgrade pip setuptools wheel -q
Write-Success "Build tools upgraded"

# ─── 6. Determine install target ──────────────────────────────
$installTarget = if ($Global) {
    Write-Header "Installing for ALL USERS (requires Administrator)"
    ""
} elseif ($useVenv) {
    Write-Header "Installing in virtual environment"
    ""  # venv doesn't need --user
} else {
    Write-Header "Installing for current user only"
    "--user"
}

# ─── 7. Install dependencies ───────────────────────────────────
Write-Header "Installing dependencies..."
$reqFile = Join-Path $repoRoot "requirements.txt"
if (Test-Path $reqFile) {
    if ($installTarget) {
        & $Python -m pip install $installTarget -r $reqFile --upgrade -q
    } else {
        & $Python -m pip install -r $reqFile --upgrade -q
    }
}
Write-Success "Dependencies installed"

# ─── 8. Install judecode package ─────────────────────────────
Write-Header "Installing judecode package..."
Push-Location $repoRoot
try {
    if ($installTarget) {
        & $Python -m pip install $installTarget -e . --upgrade -q
    } else {
        & $Python -m pip install -e . --upgrade -q
    }
} finally {
    Pop-Location
}
Write-Success "judecode installed"

# ─── 9. Create wrapper script (for venv) or add to PATH ──────
if ($useVenv) {
    Write-Header "Creating wrapper script..."
    $wrapperDir = "$env:LOCALAPPDATA\jude\bin"
    if (-not (Test-Path $wrapperDir)) {
        New-Item -ItemType Directory -Path $wrapperDir -Force | Out-Null
    }

    # Create a batch wrapper that activates venv then runs judecode
    $wrapperBat = Join-Path $wrapperDir "judecode.cmd"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    @"
@echo off
"${venvPython}" -m judecode %*
"@ | Set-Content -Path $wrapperBat -Encoding ASCII

    Write-Success "Wrapper created: $wrapperBat"

    # Add wrapper dir to PATH if not already there
    $target = "User"
    $currentPath = [Environment]::GetEnvironmentVariable("Path", $target)
    if ($currentPath -notlike "*${wrapperDir}*") {
        [Environment]::SetEnvironmentVariable("Path", "$currentPath;$wrapperDir", $target)
        Write-Success "Added $wrapperDir to PATH"
    }
    $scriptsDir = $wrapperDir
} else {
    # ─── 9b. Verify judecode is in PATH (non-venv mode) ─────
    Write-Header "Verifying installation..."

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
    $pipShow = & $Python -m pip show judecode 2>$null | Select-String "^Location:"
    if ($pipShow) {
        $loc = ($pipShow.Line -replace "^Location:\s*", "").Trim()
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
}

# ─── 10. Verify ────────────────────────────────────────────────
Write-Header "Verifying installation..."
$found = $false
try {
    if ($useVenv) {
        # Test via the wrapper
        $judecodeVersion = & $Python -m judecode --version 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Success "judecode works! Version: $judecodeVersion"
            $found = $true
        }
    } else {
        $judecodeVersion = judecode --version 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Success "judecode works! Version: $judecodeVersion"
            $found = $true
        }
    }
} catch {}

if (-not $found) {
    Write-Warn "judecode not found in PATH yet."
    Write-Warn "Restart your terminal or run: `$env:Path = [Environment]::GetEnvironmentVariable('Path', 'User')"
}

# ─── 11. Final instructions ───────────────────────────────────
Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  Jude Code installed successfully!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "`nUsage:" -ForegroundColor White
Write-Host "  judecode        Start interactive session" -ForegroundColor Gray
Write-Host "  judecode --help Show help" -ForegroundColor Gray
if ($useVenv) {
    Write-Host "`nVirtual environment: $venvDir" -ForegroundColor DarkGray
    Write-Host "  To activate manually: $venvDir\Scripts\activate" -ForegroundColor DarkGray
}
