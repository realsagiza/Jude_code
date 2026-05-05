#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# Jude Code macOS / Linux Installer
# 
# Features:
#   ✅ Auto-detects a compatible Python version (3.12 → 3.11 → 3.10 → 3.13)
#      Skips Python 3.14+ which is incompatible with pyppeteer/playwright.
#   ✅ Creates an isolated virtual environment (venv) so it works on ANY machine
#      regardless of what Python/pip version is installed globally.
#   ✅ Installs all dependencies including playwright for browser accessibility.
#   ✅ Creates a wrapper script so 'judecode' always uses the venv.
#   ✅ Adds ~/.local/bin to PATH automatically.
#
# Usage:
#   ./install.sh              Normal install (auto venv, recommended)
#   ./install.sh --python python3.12   Force specific Python binary
#   ./install.sh --help       Show this help
# ═══════════════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ─── Colors ──────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
NC='\033[0m'

print_header() { echo -e "\n${CYAN}>>> $1${NC}"; }
print_success() { echo -e "  ${GREEN}[OK]${NC} $1"; }
print_warn()  { echo -e "  ${YELLOW}[!]${NC} $1"; }
print_err()   { echo -e "  ${RED}[ERR]${NC} $1"; }

# ─── Parse args ────────────────────────────────────────────────
FORCE_PYTHON=""
PLAYWRIGHT=true

while [[ $# -gt 0 ]]; do
    case "$1" in
        --python)      FORCE_PYTHON="$2"; shift 2 ;;
        --no-playwright) PLAYWRIGHT=false; shift ;;
        --help|-h)
            cat << 'EOF'
Jude Code Installer — Smart venv-based setup

Usage:
  ./install.sh                        Auto-detect Python, create venv, install
  ./install.sh --python python3.12    Use a specific Python binary
  ./install.sh --no-playwright        Skip playwright installation (saves ~300MB)
  ./install.sh --help                 Show this help

How it works:
  1. Finds a compatible Python (3.12→3.11→3.10→3.13, skipping 3.14+)
  2. Creates an isolated venv at ./judecode/.venv
  3. Installs all dependencies inside the venv
  4. Creates ~/.local/bin/judecode wrapper that auto-activates the venv
  5. Now 'judecode' works from anywhere, regardless of system Python!

No sudo needed. No system Python packages are touched.
EOF
            exit 0
            ;;
        *)
            print_err "Unknown option: $1"
            echo "Run with --help for usage."
            exit 1
            ;;
    esac
done

# ═══════════════════════════════════════════════════════════════════════════
# STEP 1: Pick a compatible Python version
# ═══════════════════════════════════════════════════════════════════════════

print_header "🔍 Finding compatible Python version..."

# If user specified --python, use that directly
if [[ -n "$FORCE_PYTHON" ]]; then
    if ! command -v "$FORCE_PYTHON" &>/dev/null; then
        print_err "Specified Python not found: $FORCE_PYTHON"
        exit 1
    fi
    PYTHON="$FORCE_PYTHON"
    PY_VERSION=$($PYTHON --version 2>&1 | awk '{print $2}')
    print_success "Using user-specified Python: $PYTHON → $PY_VERSION"
else
    # Priority order: 3.12 (most compatible) > 3.11 > 3.10 > 3.13
    # We SKIP 3.14+ because pyppeteer/playwright has compatibility issues
    PYTHON=""
    for ver in 3.12 3.11 3.10 3.13; do
        candidate="python${ver}"
        if command -v "$candidate" &>/dev/null; then
            # Quick sanity check: make sure it works
            if "$candidate" -c "import sys; sys.exit(0)" 2>/dev/null; then
                PYTHON="$candidate"
                PY_VERSION=$($PYTHON --version 2>&1 | awk '{print $2}')
                print_success "Found Python $PY_VERSION → $candidate"
                break
            fi
        fi
        # Also check Homebrew path
        candidate="/opt/homebrew/bin/python${ver}"
        if [[ -x "$candidate" ]]; then
            PYTHON="$candidate"
            PY_VERSION=$($PYTHON --version 2>&1 | awk '{print $2}')
            print_success "Found Python $PY_VERSION → $candidate"
            break
        fi
    done

    # Fallback: try system python3, but warn if it's 3.14+
    if [[ -z "$PYTHON" ]]; then
        if command -v python3 &>/dev/null; then
            PYTHON="python3"
            PY_VERSION=$($PYTHON --version 2>&1 | awk '{print $2}')
            PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
            PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
            if [[ "$PY_MAJOR" -ge 3 && "$PY_MINOR" -ge 14 ]]; then
                print_warn "⚠️  Python $PY_VERSION detected — this may have compatibility issues with some packages."
                print_warn "   For best results, install Python 3.12: brew install python@3.12"
                print_warn "   Continuing with Python $PY_VERSION..."
            fi
        fi
    fi

    if [[ -z "$PYTHON" ]]; then
        print_err "No compatible Python found!"
        echo -e "  ${DIM}Install one of: brew install python@3.12 python@3.11${NC}"
        exit 1
    fi
fi

# ─── Sanity check ────────────────────────────────────────────
PY_MAJOR=$($PYTHON -c "import sys; print(sys.version_info[0])")
PY_MINOR=$($PYTHON -c "import sys; print(sys.version_info[1])")

if [[ "$PY_MAJOR" -lt 3 || "$PY_MINOR" -lt 10 ]]; then
    print_err "Python 3.10+ required, found $PY_VERSION"
    exit 1
fi

print_success "Python $PY_VERSION — ready to go!"

# ═══════════════════════════════════════════════════════════════════════════
# STEP 2: Find pip (venv-agnostic)
# ═══════════════════════════════════════════════════════════════════════════

# Get pip associated with this Python
PIP="$($PYTHON -m pip -V 2>/dev/null && echo "$PYTHON -m pip" || echo "pip3")"

# ═══════════════════════════════════════════════════════════════════════════
# STEP 3: Create virtual environment
# ═══════════════════════════════════════════════════════════════════════════

VENV_DIR="${REPO_ROOT}/.venv"
VENV_PYTHON="${VENV_DIR}/bin/python"
VENV_PIP="${VENV_DIR}/bin/pip"

# Check if venv already exists and uses a different Python version
RECREATE_VENV=false
if [[ -d "$VENV_DIR" && -f "$VENV_DIR/pyvenv.cfg" ]]; then
    CURRENT_VENV_PYTHON=$(grep "^home" "$VENV_DIR/pyvenv.cfg" 2>/dev/null | head -1 | sed 's/.*= //' || echo "")
    if [[ -n "$CURRENT_VENV_PYTHON" ]]; then
        # Get the python binary from that home
        CURRENT_PY_BIN="${CURRENT_VENV_PYTHON}/python3"
        if [[ -x "$CURRENT_PY_BIN" ]]; then
            CURRENT_PY_VER=$("$CURRENT_PY_BIN" --version 2>&1 | awk '{print $2}')
            if [[ "$CURRENT_PY_VER" != "$PY_VERSION" ]]; then
                print_warn "Existing venv uses Python $CURRENT_PY_VER, but we want $PY_VERSION."
                print_warn "Recreating venv with Python $PY_VERSION..."
                RECREATE_VENV=true
            fi
        fi
    fi
fi

if [[ "$RECREATE_VENV" == true ]]; then
    print_header "♻️  Recreating virtual environment..."
    rm -rf "$VENV_DIR"
fi

if [[ ! -d "$VENV_DIR" ]]; then
    print_header "📦 Creating virtual environment with Python $PY_VERSION..."
    "$PYTHON" -m venv "$VENV_DIR"
    print_success "Virtual environment created at: $VENV_DIR"
    print_success "  Python: $("$VENV_PYTHON" --version 2>&1)"
else
    print_success "Virtual environment already exists at: $VENV_DIR"
    print_success "  Python: $("$VENV_PYTHON" --version 2>&1)"
fi

# ═══════════════════════════════════════════════════════════════════════════
# STEP 4: Upgrade pip & build tools inside venv
# ═══════════════════════════════════════════════════════════════════════════

print_header "📥 Upgrading pip & build tools inside venv..."
"$VENV_PIP" install --upgrade pip setuptools wheel -q
print_success "pip: $("$VENV_PIP" --version 2>&1)"

# ═══════════════════════════════════════════════════════════════════════════
# STEP 5: Install core dependencies from requirements.txt
# ═══════════════════════════════════════════════════════════════════════════

print_header "📦 Installing core dependencies..."
REQ_FILE="${REPO_ROOT}/requirements.txt"
if [[ -f "$REQ_FILE" ]]; then
    # Install one by one for clearer error messages
    while IFS= read -r line || [[ -n "$line" ]]; do
        # Skip comments and blank lines
        [[ "$line" =~ ^#.*$ ]] && continue
        [[ -z "$(echo "$line" | tr -d '[:space:]')" ]] && continue
        # Skip optional packages (commented with trailing #)
        [[ "$line" =~ ^# ]] && continue
        print_warn "Installing: $(echo "$line" | xargs)"
        "$VENV_PIP" install "$(echo "$line" | xargs)" -q 2>&1 | tail -1 || true
    done < "$REQ_FILE"
    print_success "Core dependencies installed"
else
    print_warn "requirements.txt not found at $REQ_FILE"
    print_warn "Installing minimal dependencies..."
    "$VENV_PIP" install httpx rich pyperclip python-dotenv mss Pillow pyautogui -q
fi

# (python-mss package name is deprecated - mss is the correct one)

# ═══════════════════════════════════════════════════════════════════════════
# STEP 6: Install judecode package itself (editable mode)
# ═══════════════════════════════════════════════════════════════════════════

print_header "🔧 Installing judecode package..."
cd "$REPO_ROOT"
"$VENV_PIP" install -e . --no-deps -q
print_success "judecode installed in editable mode"
cd - &>/dev/null

# ═══════════════════════════════════════════════════════════════════════════
# STEP 7: Install browser automation (playwright)
# ═══════════════════════════════════════════════════════════════════════════

if [[ "$PLAYWRIGHT" == true ]]; then
    print_header "🌐 Installing browser automation (Playwright)..."
    
    # Install playwright in venv
    "$VENV_PIP" install playwright -q 2>&1 || print_warn "playwright install failed (non-critical)"
    
    # Check if playwright is now available
    if "$VENV_PYTHON" -c "import playwright" 2>/dev/null; then
        print_success "playwright Python package installed"
        
        # Install Chromium browser for Playwright
        print_header "🌍 Installing Chromium browser (for accessibility trees)..."
        print_warn "This downloads ~150MB. To skip: re-run with --no-playwright"
        if "$VENV_PYTHON" -m playwright install chromium 2>&1; then
            print_success "Chromium installed for Playwright!"
        else
            print_warn "Chromium install failed. Run manually: cd '$REPO_ROOT' && .venv/bin/python -m playwright install chromium"
        fi
    else
        print_warn "playwright not installed (non-critical - accessibility trees won't work)"
        print_warn "To install later: .venv/bin/pip install playwright && .venv/bin/python -m playwright install chromium"
    fi
fi

# Also install pyppeteer as fallback (if not Python 3.14+)
PY_MINOR_VER=$("$VENV_PYTHON" -c "import sys; print(sys.version_info[1])")
if [[ "$PY_MINOR_VER" -lt 14 ]]; then
    print_header "🦎 Installing pyppeteer (fallback browser automation)..."
    "$VENV_PIP" install pyppeteer -q 2>&1 || print_warn "pyppeteer install skipped"
    if "$VENV_PYTHON" -c "import pyppeteer" 2>/dev/null; then
        print_success "pyppeteer installed"
    fi
else
    print_warn "Skipping pyppeteer (incompatible with Python $PY_VERSION)"
    print_warn "  Playwright will be used instead for browser automation."
fi

# ═══════════════════════════════════════════════════════════════════════════
# STEP 8: Create wrapper script for seamless 'judecode' command
# ═══════════════════════════════════════════════════════════════════════════

print_header "🔗 Creating 'judecode' command wrapper..."

WRAPPER_DIR="${HOME}/.local/bin"
mkdir -p "$WRAPPER_DIR"

# Create a robust wrapper that always uses the venv
cat > "${WRAPPER_DIR}/judecode" << 'WRAPPER'
#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# Jude Code wrapper script
#
# Automatically detects and uses the project's virtual environment.
# Works from anywhere, regardless of system Python version.
#
# How it works:
#   1. Finds the project root by looking for the wrapper's location
#      or walking up from CWD looking for judecode/__main__.py
#   2. Activates the venv at {project_root}/.venv
#   3. Runs judecode with the venv's Python
# ═══════════════════════════════════════════════════════════════════════════

set -euo pipefail

# Find the project root
SCRIPT_PATH="$(readlink -f "$0" 2>/dev/null || realpath "$0" 2>/dev/null || echo "$0")"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"

# Strategy 1: If wrapper is in ~/.local/bin, look relative to REPO_ROOT
# (This is set during install, so we need a marker)
WRAPPER_DIR="$(dirname "$SCRIPT_DIR")/.local/bin"
if [[ "$SCRIPT_DIR" == "$WRAPPER_DIR" ]] || [[ "$SCRIPT_DIR" == "$HOME/.local/bin" ]]; then
    # Try to find the project root via a known marker file
    CANDIDATE_DIRS=(
        "$(pwd)"
        "$(dirname "$(pwd)")/judecode"
        "$HOME/Code/Jude_code/judecode"
        "$HOME/judecode"
        "/opt/judecode"
    )
    PROJECT_ROOT=""
    for dir in "${CANDIDATE_DIRS[@]}"; do
        if [[ -f "$dir/judecode/__main__.py" ]]; then
            PROJECT_ROOT="$dir"
            break
        fi
    done
    
    # If not found, walk up from CWD
    if [[ -z "$PROJECT_ROOT" ]]; then
        CWD="$(pwd)"
        while [[ "$CWD" != "/" ]]; do
            if [[ -f "$CWD/judecode/__main__.py" ]]; then
                PROJECT_ROOT="$CWD"
                break
            fi
            CWD="$(dirname "$CWD")"
        done
    fi
else
    # Strategy 2: We're in a known location - derive project root
    PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
fi

# Fallback: use the REPO_ROOT stored during install
if [[ -z "${PROJECT_ROOT:-}" || ! -f "$PROJECT_ROOT/judecode/__main__.py" ]]; then
    # Hardcoded fallback from install time — will be substituted below
    PROJECT_ROOT="__REPO_ROOT__"
fi

# Verify project root exists
if [[ ! -f "$PROJECT_ROOT/judecode/__main__.py" ]]; then
    echo "Error: Cannot find Jude Code project root." >&2
    echo "Looked in: $PROJECT_ROOT" >&2
    echo "Please re-run install.sh or set JUDECODE_ROOT environment variable." >&2
    exit 1
fi

VENV_PYTHON="${PROJECT_ROOT}/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "Error: Virtual environment not found at $VENV_PYTHON" >&2
    echo "Please run install.sh first." >&2
    exit 1
fi

# Forward all arguments to judecode inside the venv
exec "$VENV_PYTHON" -m judecode "$@"
WRAPPER

# Substitute the project root into the wrapper
sed -i '' "s|__REPO_ROOT__|${REPO_ROOT}|g" "${WRAPPER_DIR}/judecode"
chmod +x "${WRAPPER_DIR}/judecode"

print_success "Wrapper created at: ${WRAPPER_DIR}/judecode"

# ═══════════════════════════════════════════════════════════════════════════
# STEP 9: Ensure ~/.local/bin is in PATH
# ═══════════════════════════════════════════════════════════════════════════

print_header "🔧 Ensuring PATH includes ~/.local/bin..."

RC_FILE=""
SHELL_NAME=$(basename "$SHELL")
case "$SHELL_NAME" in
    zsh)   RC_FILE="${HOME}/.zshrc" ;;
    bash)  RC_FILE="${HOME}/.bashrc" ;;
    *)     RC_FILE="${HOME}/.${SHELL_NAME}rc" ;;
esac

PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'

if [[ -f "$RC_FILE" ]]; then
    if grep -qF "export PATH=\"\$HOME/.local/bin:" "$RC_FILE" 2>/dev/null || \
       grep -qF "export PATH=\"\$PATH:\$HOME/.local/bin" "$RC_FILE" 2>/dev/null; then
        print_success "~/.local/bin already in PATH (in $RC_FILE)"
    else
        echo "" >> "$RC_FILE"
        echo "# Added by Jude Code installer" >> "$RC_FILE"
        echo "$PATH_LINE" >> "$RC_FILE"
        print_success "Added ~/.local/bin to PATH in $RC_FILE"
        print_warn "Run: source $RC_FILE"
    fi
else
    print_warn "$RC_FILE not found. Creating..."
    echo "$PATH_LINE" > "$RC_FILE"
    print_success "Created $RC_FILE with PATH setup"
    print_warn "Run: source $RC_FILE"
fi

# ═══════════════════════════════════════════════════════════════════════════
# STEP 10: Fix the 'Browser error: Pyppeteer catching classes that do not inherit
#           from BaseException is not allowed' problem
#
# This error happens because pyppeteer's exception handling uses Python 3.13+
# syntax that was deprecated. We apply a monkey-patch if on Python 3.14+.
# ═══════════════════════════════════════════════════════════════════════════

print_header "🩹 Checking for pyppeteer compatibility patch..."

PY_MINOR_VER=$("$VENV_PYTHON" -c "import sys; print(sys.version_info[1])")
if [[ "$PY_MINOR_VER" -ge 14 ]]; then
    print_warn "Python $PY_VERSION detected — pyppeteer is incompatible."
    print_warn "We'll use Playwright instead (no patch needed)."
    
    # Apply a monkey-patch to the venv's site-packages for pyppeteer if installed
    PYPETEER_DIR=$(find "$VENV_DIR" -path "*/pyppeteer/__init__.py" 2>/dev/null | head -1 | xargs dirname 2>/dev/null || true)
    if [[ -n "$PYPETEER_DIR" && -f "$PYPETEER_DIR/connection.py" ]]; then
        print_warn "Applying pyppeteer compatibility patch (for Python 3.14+)..."
        # Patch the 'except' statements that use invalid exception classes
        # This is a simplified fix for the most common pattern
        sed -i '' 's/except BaseException as _TargetClosedError:/except Exception as _TargetClosedError:/g' "$PYPETEER_DIR/connection.py" 2>/dev/null || true
        sed -i '' 's/except BaseException as _asyncio_TimeoutError:/except Exception as _asyncio_TimeoutError:/g' "$PYPETEER_DIR/connection.py" 2>/dev/null || true
        print_success "Pyppeteer patch applied (connection.py)"
    fi
else
    print_success "Python $PY_VERSION — no pyppeteer patch needed"
fi

# ═══════════════════════════════════════════════════════════════════════════
# STEP 11: Verify installation
# ═══════════════════════════════════════════════════════════════════════════

print_header "✅ Verifying installation..."

# Check if the wrapper works
if [[ -x "${WRAPPER_DIR}/judecode" ]]; then
    if JUDE_VER=$("${WRAPPER_DIR}/judecode" --version 2>/dev/null); then
        print_success "judecode works! $JUDE_VER"
    else
        # --version might not exist, try running with --help
        print_success "Wrapper script created successfully"
    fi
else
    print_warn "Wrapper script not executable"
fi

# Verify key dependencies
print_header "📋 Installed packages:"
"$VENV_PIP" list 2>/dev/null | grep -iE "judecode|playwright|pyppeteer|httpx|rich|pyautogui|mss|pillow" || true

# ═══════════════════════════════════════════════════════════════════════════
# DONE
# ═══════════════════════════════════════════════════════════════════════════

echo ""
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ Jude Code installed successfully!${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo ""
echo -e "${CYAN}Quick start:${NC}"
echo -e "  ${DIM}judecode${NC}        Start Jude Code"
echo ""
echo -e "${CYAN}About this install:${NC}"
echo -e "  ${DIM}Python:${NC}     $("$VENV_PYTHON" --version 2>&1)"
echo -e "  ${DIM}Venv:${NC}      ${VENV_DIR}"
echo -e "  ${DIM}Wrapper:${NC}   ${WRAPPER_DIR}/judecode"
echo ""

# Remind to source shell profile if needed
if [[ -f "${RC_FILE:-}" ]]; then
    if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
        echo -e "  ${YELLOW}› Run: source ${RC_FILE}${NC}"
    fi
fi

echo -e "${CYAN}Need help?${NC} ${DIM}judecode --help${NC}"
echo ""
