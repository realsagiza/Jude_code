@echo off
:: Jude Code Windows Installer Wrapper
:: Usage: install.bat          (user install, no admin needed)
:: Usage: install.bat /global  (all users, requires admin)

title Jude Code Installer

net session >nul 2>&1
if %errorLevel% == 0 (
    echo [INFO] Running as Administrator
    set "IS_ADMIN=1"
) else (
    set "IS_ADMIN=0"
)

if "%~1"=="/global" (
    if "%IS_ADMIN%"=="0" (
        echo [ERROR] Global install requires Administrator privileges.
        echo Please right-click this file and select "Run as administrator"
        pause
        exit /b 1
    )
    echo [INFO] Installing for ALL USERS...
    powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0install.ps1" -Global
) else (
    echo [INFO] Installing for current user only...
    echo [INFO] For global install, run: install.bat /global
    echo.
    powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0install.ps1"
)

if %errorLevel% neq 0 (
    echo.
    echo [ERROR] Installation failed.
    pause
    exit /b %errorLevel%
)

echo.
echo [OK] Done! You may need to restart your terminal.
pause
