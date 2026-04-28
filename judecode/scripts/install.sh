#!/usr/bin/env bash
# Jude Code macOS / Linux Installer
# Handles PEP 668 externally-managed Python by creating a virtual environment.
# Usage: ./install.sh              (user install, no sudo needed)
# Usage: ./install.sh --global     (system-wide, requires sudo + --break-system-packages)

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
GLOBAL=false
PYTHON="python3"
BREAK_SYSTEM_PACKAGES=false
FORCE_VENV=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --global)      GLOBAL=true; shift ;;
        --venv)        FORCE_VENV=true; shift ;;
        --python)      PYTHON="$2"; shift 2 ;;
        --break-system-packages) BREAK_SYSTEM_PACKAGES=true; shift ;;
        --help|-h)
            cat << 'EOF'
Jude Code Installer

Usage:
  ./install.sh              User install (auto-detects PEP 668)
  ./install.sh --venv       Force virtual environment install
  ./install.sh --global     System-wide (requires sudo + --break-system-packages)
  ./install.sh --python /path/to/python3   Use a specific Python
  ./install.sh --break-system-packages     Bypass PEP 668 (not recommended)
  ./install.sh --help       Show this help

Recommended:  Run without any flags and the installer will choose
              the safest method (venv for PEP 668, normal pip otherwise).
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

# ─── 1. Check Python ───────────────────────────────────────────
print_header "Checking Python..."
if ! command -v "$PYTHON" &>/dev/null; then
    print_err "Python not found: $PYTHON"
    echo -e "  ${DIM}brew install python@3.12${NC}"
    exit 1
fi

PY_VERSION=$($PYTHON --version 2>&1 | awk '{print $2}')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [[ "$PY_MAJOR" -lt 3 || "$PY_MINOR" -lt 10 ]]; then
    print_err "Python 3.10+ required, found $PY_VERSION"
    exit 1
fi
print_success "Python $PY_VERSION found"

# ─── 2. Detect externally-managed (PEP 668) ─────────────────
EXTERNALLY_MANAGED=false
if "$PYTHON" -m pip install --dry-run fakepkg 2>/dev/null | grep -q "externally-managed"; then
    EXTERNALLY_MANAGED=true
elif [[ -f "$($PYTHON -c 'import sysconfig; print(sysconfig.get_path("stdlib"))')/EXTERNALLY-MANAGED" ]]; then
    EXTERNALLY_MANAGED=true
fi

use_venv=false
if [[ "$EXTERNALLY_MANAGED" == true ]]; then
    print_warn "System Python is externally managed (PEP 668)."
    if [[ "$FORCE_VENV" == true ]]; then
        print_warn "Forced --venv mode."
        use_venv=true
    elif [[ "$GLOBAL" == true && "$BREAK_SYSTEM_PACKAGES" == true ]]; then
        print_warn "Using --break-system-packages as requested."
        use_venv=false
    elif [[ "$GLOBAL" == true ]]; then
        print_err "System-wide install is blocked by PEP 668."
        print_warn "Please use one of these options:"
        echo -e "  ${DIM}1. ./install.sh                (recommended, auto venv)${NC}"
        echo -e "  ${DIM}2. ./install.sh --venv         (force virtual env)${NC}"
        echo -e "  ${DIM}3. ./install.sh --global --break-system-packages  (risky)${NC}"
        exit 1
    else
        use_venv=true
    fi
fi

# ─── 3. Set up venv (if needed) ──────────────────────────────
if [[ "$use_venv" == true ]]; then
    VENV_DIR="${REPO_ROOT}/.venv"
    print_header "Creating virtual environment..."
    if [[ ! -d "$VENV_DIR" ]]; then
        "$PYTHON" -m venv "$VENV_DIR"
    fi
    # Re-point to venv python/pip
    PYTHON="$VENV_DIR/bin/python"
    PIP="$VENV_DIR/bin/pip"
    print_success "Virtual environment ready: $VENV_DIR"
else
    PIP="$PYTHON -m pip"
fi

# ─── 4. Upgrade pip & build tools ────────────────────────────
print_header "Upgrading build tools..."
$PIP install --upgrade pip setuptools wheel -q
print_success "Build tools upgraded"

# ─── 5. Install dependencies ─────────────────────────────────
print_header "Installing dependencies..."
REQ_FILE="${REPO_ROOT}/requirements.txt"
if [[ -f "$REQ_FILE" ]]; then
    $PIP install -r "$REQ_FILE" --upgrade -q
    print_success "Dependencies installed"
else
    print_warn "requirements.txt not found, skipping"
fi

# ─── 6. Install judecode ───────────────────────────────────
print_header "Installing judecode package..."
cd "$REPO_ROOT"
$PIP install -e . --upgrade -q
print_success "judecode installed"
cd - &>/dev/null

# ─── 7. Find the judecode executable ────────────────────────
print_header "Detecting executable directory..."

if [[ "$use_venv" == true ]]; then
    BIN_DIR="$VENV_DIR/bin"
    WRAPPER_DIR="${HOME}/.local/bin"
    # Create wrapper script so judecode works without activating venv
    mkdir -p "$WRAPPER_DIR"
    cat > "${WRAPPER_DIR}/judecode" << EOF
#!/usr/bin/env bash
# Jude Code wrapper — auto-activates venv before running
exec "$VENV_DIR/bin/python" -m judecode "\$@"
EOF
    chmod +x "${WRAPPER_DIR}/judecode"
    print_success "Wrapper created at ~/.local/bin/judecode"
else
    # Normal pip install detection
    BIN_DIR=$($PYTHON -c "import site,sys,os; print(os.path.join(os.path.dirname(site.getusersitepackages()), 'Scripts' if sys.platform.startswith('win') else 'bin'))" 2>/dev/null)
    if [[ -z "$BIN_DIR" || ! -d "$BIN_DIR" ]]; then
        BIN_DIR="$(dirname "$($PYTHON -c 'import sys; print(sys.executable)')")"
        [[ -d "${BIN_DIR}/../bin" ]] && BIN_DIR="$(cd "${BIN_DIR}/../bin" && pwd)"
    fi
fi

print_success "Executable directory: ${BIN_DIR}"

# ─── 8. Ensure PATH includes ~/.local/bin ───────────────────
RC_FILE=""
SHELL_NAME=$(basename "$SHELL")
case "$SHELL_NAME" in
    zsh)
        RC_FILE="${HOME}/.zshrc"
        ;;
    bash)
        RC_FILE="${HOME}/.bash_profile"
        [[ -f "${HOME}/.bashrc" ]] && RC_FILE="${HOME}/.bashrc"
        ;;
    *)
        RC_FILE="${HOME}/.${SHELL_NAME}rc"
        ;;
esac

if [[ -f "$RC_FILE" && -n "${WRAPPER_DIR:-}" ]]; then
    if [[ ":$PATH:" != *":${WRAPPER_DIR}:"* ]]; then
        if ! grep -qF "export PATH=\"\$PATH:${WRAPPER_DIR}\"" "$RC_FILE" 2>/dev/null; then
            echo "" >> "$RC_FILE"
            echo "# Added by Jude Code installer" >> "$RC_FILE"
            echo "export PATH=\"\$PATH:${WRAPPER_DIR}\"" >> "$RC_FILE"
            print_success "Added ${WRAPPER_DIR} to ${RC_FILE}"
        fi
    fi
fi

# ─── 9. Verify ───────────────────────────────────────────────
print_header "Verifying installation..."
JUDE_PATH="${WRAPPER_DIR:-$BIN_DIR}/judecode"
if [[ -x "$JUDE_PATH" ]] && "$JUDE_PATH" --version &>/dev/null; then
    JUDE_VER=$("$JUDE_PATH" --version 2>/dev/null || echo "unknown")
    print_success "judecode works! Version: $JUDE_VER"
else
    print_warn "judecode not in PATH yet."
    print_warn "Source your shell profile or restart your terminal."
fi

# ─── 10. Done ────────────────────────────────────────────────
echo -e "\n${CYAN}========================================${NC}"
echo -e "${GREEN}  Jude Code installed successfully!${NC}"
echo -e "${CYAN}========================================${NC}"
echo -e "\n${CYAN}Usage:${NC}"
echo -e "  ${DIM}judecode        Start interactive session${NC}"
echo -e "\n${YELLOW}Note:${NC}"
if [[ -n "${RC_FILE:-}" ]]; then
    echo -e "  ${DIM}Run: source ${RC_FILE}${NC}"
fi
if [[ "$use_venv" == true ]]; then
    echo -e "  ${DIM}Virtual env: ${VENV_DIR}${NC}"
fi
