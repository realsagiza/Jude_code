@echo off
REM ============================================================
REM  Build script for Jude Code on Windows
REM  Creates a standalone .exe with PyInstaller
REM ============================================================
title Jude Code - Windows Build

echo.
echo  ============================================
echo    Jude Code - Windows Build Script
echo  ============================================
echo.

REM ── Step 1: Check Python ──
echo [1/6] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python 3.10+ from https://www.python.org/downloads/
    pause
    exit /b 1
)
echo   Python found: 
python --version
echo.

REM ── Step 2: Create virtual environment ──
echo [2/6] Creating virtual environment...
if not exist "venv" (
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo   Virtual environment created.
) else (
    echo   Virtual environment already exists.
)
echo.

REM ── Step 3: Activate venv and install dependencies ──
echo [3/6] Installing dependencies...
call venv\Scripts\activate.bat

REM Install core dependencies
python -m pip install --upgrade pip -q
pip install -r requirements.txt -q

REM Install build tools
pip install pyinstaller -q

REM Install optional dependencies for full functionality
pip install openpyxl xlsxwriter pdfplumber pdfminer.six pypdfium2 -q

echo   Dependencies installed.
echo.

REM ── Step 4: Create icon (if not exists) ──
echo [4/6] Checking icon...
if not exist "judecode.ico" (
    echo   No icon found. Creating a placeholder...
    REM We'll skip icon for now, you can add judecode.ico later
    echo   (Optional: Add judecode.ico to this folder for custom icon)
)
echo.

REM ── Step 5: Build with PyInstaller ──
echo [5/6] Building executable with PyInstaller...
echo   This may take a few minutes...
echo.

pyinstaller --clean ^
    --name "judecode" ^
    --console ^
    --onefile ^
    --add-data "judecode;judecode" ^
    --hidden-import "httpx" ^
    --hidden-import "rich" ^
    --hidden-import "rich.markdown" ^
    --hidden-import "rich.panel" ^
    --hidden-import "rich.align" ^
    --hidden-import "rich.columns" ^
    --hidden-import "rich.rule" ^
    --hidden-import "rich.text" ^
    --hidden-import "rich.box" ^
    --hidden-import "rich.syntax" ^
    --hidden-import "rich.tree" ^
    --hidden-import "rich.table" ^
    --hidden-import "rich.live" ^
    --hidden-import "rich.progress" ^
    --hidden-import "rich.prompt" ^
    --hidden-import "pyperclip" ^
    --hidden-import "mss" ^
    --hidden-import "PIL" ^
    --hidden-import "PIL.Image" ^
    --hidden-import "PIL.ImageGrab" ^
    --hidden-import "pyautogui" ^
    --hidden-import "pytweening" ^
    --hidden-import "pyscreeze" ^
    --hidden-import "pyrect" ^
    --hidden-import "mouseinfo" ^
    --hidden-import "pymsgbox" ^
    --hidden-import "anyio" ^
    --hidden-import "anyio._backends._asyncio" ^
    --hidden-import "anyio.streams.file" ^
    --hidden-import "anyio.streams.memory" ^
    --hidden-import "anyio.streams.stapled" ^
    --hidden-import "anyio.streams.text" ^
    --hidden-import "anyio.streams.tls" ^
    --hidden-import "anyio.to_thread" ^
    --hidden-import "httpcore" ^
    --hidden-import "httpcore._async.connection" ^
    --hidden-import "httpcore._async.connection_pool" ^
    --hidden-import "httpcore._async.http11" ^
    --hidden-import "httpcore._sync.connection" ^
    --hidden-import "httpcore._sync.connection_pool" ^
    --hidden-import "httpcore._sync.http11" ^
    --hidden-import "h11" ^
    --hidden-import "idna" ^
    --hidden-import "certifi" ^
    --hidden-import "charset_normalizer" ^
    --hidden-import "markdown_it" ^
    --hidden-import "mdurl" ^
    --hidden-import "wcwidth" ^
    --hidden-import "cryptography" ^
    --hidden-import "pdfminer" ^
    --hidden-import "pdfminer.pdfdocument" ^
    --hidden-import "pdfminer.pdfparser" ^
    --hidden-import "pdfminer.pdfinterp" ^
    --hidden-import "pdfminer.pdfpage" ^
    --hidden-import "pdfminer.pdftypes" ^
    --hidden-import "pdfminer.pdfdevice" ^
    --hidden-import "pdfminer.converter" ^
    --hidden-import "pdfminer.cmapdb" ^
    --hidden-import "pdfminer.encodingdb" ^
    --hidden-import "pdfminer.layout" ^
    --hidden-import "pdfminer.utils" ^
    --hidden-import "pdfplumber" ^
    --hidden-import "pdfplumber.page" ^
    --hidden-import "pdfplumber.utils" ^
    --hidden-import "pdfplumber.table" ^
    --hidden-import "pdfplumber.display" ^
    --hidden-import "openpyxl" ^
    --hidden-import "xlsxwriter" ^
    --hidden-import "pypdfium2" ^
    --hidden-import "pypdfium2._helpers" ^
    --hidden-import "pypdfium2.raw" ^
    --exclude-module "tkinter" ^
    --exclude-module "PyQt5" ^
    --exclude-module "PyQt6" ^
    --exclude-module "PySide2" ^
    --exclude-module "PySide6" ^
    --exclude-module "setuptools.tests" ^
    --exclude-module "pip._internal.tests" ^
    --exclude-module "AppKit" ^
    --exclude-module "Foundation" ^
    --exclude-module "CoreFoundation" ^
    --exclude-module "Quartz" ^
    --exclude-module "Cocoa" ^
    --exclude-module "PyObjCTools" ^
    --exclude-module "rubicon" ^
    --exclude-module "objc" ^
    judecode\__main__.py

if %errorlevel% neq 0 (
    echo [ERROR] PyInstaller build failed!
    pause
    exit /b 1
)
echo.
echo   Build successful!
echo.

REM ── Step 6: Verify ──
echo [6/6] Verifying build...
if exist "dist\judecode.exe" (
    echo   ✓ judecode.exe created successfully!
    echo.
    echo   Location: %cd%\dist\judecode.exe
    echo   Size: 
    for %%I in ("dist\judecode.exe") do echo     %%~zI bytes
    echo.
    echo   You can now run it directly from dist\judecode.exe
    echo   Or distribute it to other Windows machines (no Python needed!)
) else (
    echo   [WARNING] judecode.exe not found in dist\ folder
    echo   Check the dist\ folder for the output.
)
echo.

echo  ============================================
echo    Build Complete!
echo  ============================================
echo.
pause
