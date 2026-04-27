#!/usr/bin/env bash
# Jude Code macOS Installer
# Usage: ./install.sh           (user install, no sudo needed)
# Usage: ./install.sh --global  (system-wide install, may require sudo)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ─── Colors ──────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
NC='\033[0m' # No Color

print_header() { echo -e "\n${CYAN}>>> $1${NC}"; }
print_success() { echo -e "  ${GREEN}[OK]${NC} $1"; }
print_warn()  { echo -e "  ${YELLOW}[!]${NC} $1"; }
print_err()   { echo -e "  ${RED}[ERR]${NC} $1"; }

# ─── Parse args ────────────────────────────────────────────────
GLOBAL=false
PYTHON="python3"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --global)
            GLOBAL=true
            shift
            ;;
        --python)
            PYTHON="$2"
            shift 2
            ;;
        --help|-h)
            cat << 'EOF'
Jude Code macOS Installer

Usage:
  ./install.sh              Install for current user (recommended)
  ./install.sh --global     Install system-wide (may require sudo)
  ./install.sh --python /path/to/python3  Use a specific Python
  ./install.sh --help       Show this help

Note:
  --global requires write access to system Python site-packages.
  For most users, user install (--global omitted) is preferred.
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
    echo -e "  ${DIM}Please install Python 3.10+ from https://www.python.org/downloads/${NC}"
    echo -e "  ${DIM}Or use: brew install python@3.12${NC}"
    exit 1
fi

PY_VERSION=$($PYTHON --version 2>&1 | awk '{print $2}')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [[ "$PY_MAJOR" -lt 3 || ( "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 10 ) ]]; then
    print_err "Python 3.10+ required, but found $PY_VERSION"
    echo -e "  ${DIM}Please install a newer Python version.${NC}"
    exit 1
fi
print_success "Python $PY_VERSION found ($($PYTHON -c 'import sys; print(sys.executable)'))"

# ─── 2. Check pip ────────────────────────────────────────────
print_header "Checking pip..."
if ! $PYTHON -m pip --version &>/dev/null; then
    print_warn "pip not found. Installing..."
    $PYTHON -m ensurepip --upgrade 2>/dev/null || {
        print_err "Failed to install pip. Please install manually."
        exit 1
    }
    print_success "pip installed"
else
    print_success "pip available"
fi

# ─── 3. Check Git (optional) ─────────────────────────────────
print_header "Checking Git..."
if command -v git &>/dev/null; then
    GIT_VER=$(git --version)
    print_success "Git found: $GIT_VER"
else
    print_warn "Git not found. Some features may not work."
    echo -e "  ${DIM}Install with: brew install git${NC}"
fi

# ─── 4. Upgrade build tools ──────────────────────────────────
print_header "Upgrading build tools..."
$PYTHON -m pip install --upgrade pip setuptools wheel -q
print_success "Build tools upgraded"

# ─── 5. Determine install target ─────────────────────────────
if [[ "$GLOBAL" == true ]]; then
    print_header "Installing for ALL USERS (system-wide)"
    INSTALL_FLAG=""
else
    print_header "Installing for current user only"
    INSTALL_FLAG="--user"
fi

# ─── 6. Install dependencies ───────────────────────────────────
print_header "Installing dependencies..."
REQ_FILE="${REPO_ROOT}/requirements.txt"
if [[ -f "$REQ_FILE" ]]; then
    $PYTHON -m pip install $INSTALL_FLAG -r "$REQ_FILE" --upgrade -q
    print_success "Dependencies installed"
else
    print_warn "requirements.txt not found, skipping"
fi

# ─── 7. Install judecode package ─────────────────────────────
print_header "Installing judecode package..."
cd "$REPO_ROOT"
$PYTHON -m pip install $INSTALL_FLAG -e . --upgrade -q
print_success "judecode installed"
cd - &>/dev/null

# ─── 8. Detect scripts/bin directory ───────────────────────────
print_header "Detecting executable directory..."

# Try to get the scripts directory from pip
BIN_DIR=$($PYTHON -c "import site, sys, pathlib; print(site.getusersitepackages())" 2>/dev/null)
if [[ -n "$BIN_DIR" ]]; then
    # user site-packages/../Scripts or ../bin
    PARENT="$(dirname "$BIN_DIR")"
    for CANDIDATE in "${PARENT}/Scripts" "${PARENT}/bin"; do
        if [[ -d "$CANDIDATE" ]]; then
            BIN_DIR="$CANDIDATE"
            break
        fi
    done
fi

# Fallback: try pip show
if [[ -z "$BIN_DIR" || ! -d "$BIN_DIR" ]]; then
    PIP_LOC=$($PYTHON -m pip show judecode 2>/dev/null | grep "^Location:" | awk '{print $2}')
    if [[ -n "$PIP_LOC" ]]; then
        PARENT="$(dirname "$PIP_LOC")"
        for CANDIDATE in "${PARENT}/Scripts" "${PARENT}/bin"; do
            if [[ -d "$CANDIDATE" ]]; then
                BIN_DIR="$CANDIDATE"
                break
            fi
        done
    fi
fi

# Fallback: derive from python executable
if [[ -z "$BIN_DIR" || ! -d "$BIN_DIR" ]]; then
    PY_BIN=$(dirname "$($PYTHON -c 'import sys; print(sys.executable)')")
    for CANDIDATE in "${PY_BIN}" "${PY_BIN}/../bin"; do
        if [[ -d "$CANDIDATE" ]]; then
            BIN_DIR="$(cd "$CANDIDATE" && pwd)"
            break
        fi
    done
fi

print_success "Executable directory: ${BIN_DIR:-unknown}"

# ─── 9. Add to PATH if needed ────────────────────────────────
if [[ -n "$BIN_DIR" && -d "$BIN_DIR" ]]; then
    # Check if bin_dir is already in PATH
    if [[ ":$PATH:" != *":${BIN_DIR}:"* ]]; then
        print_header "Adding to PATH..."
        # Determine which shell config to update
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

        # Add to rc file if not already there
        if [[ -f "$RC_FILE" ]]; then
            if ! grep -qF "export PATH=\"\$PATH:${BIN_DIR}\"" "$RC_FILE" 2>/dev/null; then
                echo "" >> "$RC_FILE"
                echo "# Added by Jude Code installer" >> "$RC_FILE"
                echo "export PATH=\"\$PATH:${BIN_DIR}\"" >> "$RC_FILE"
                print_success "Added to ${RC_FILE}"
            else
                print_success "Already in ${RC_FILE}"
            fi
        else
            print_warn "Could not find shell rc file. Please add manually:"
            echo -e "  ${DIM}export PATH=\"\$PATH:${BIN_DIR}\"${NC}"
        fi
    else
        print_success "Already in PATH"
    fi
fi

# ─── 10. Verify installation ───────────────────────────────────
print_header "Verifying installation..."
if command -v judecode &>/dev/null; then
    JUDE_VER=$(judecode --version 2>/dev/null || echo "unknown")
    print_success "judecode command works! Version: $JUDE_VER"
else
    print_warn "judecode not found in current PATH."
    if [[ -n "$BIN_DIR" && -x "${BIN_DIR}/judecode" ]]; then
        print_success "Found at: ${BIN_DIR}/judecode"
        print_warn "Please restart your terminal or run:"
        echo -e "  ${DIM}source ${RC_FILE:-your shell rc file}${NC}"
        echo -e "  ${DIM}# OR${NC}"
        echo -e "  ${DIM}export PATH=\"\$PATH:${BIN_DIR}\"${NC}"
    else
        print_err "judecode executable not found."
        exit 1
    fi
fi

# ─── 11. Done ────────────────────────────────────────────────
echo -e "\n${CYAN}========================================${NC}"
echo -e "${GREEN}  Jude Code installed successfully!${NC}"
echo -e "${CYAN}========================================${NC}"
echo -e "\n${CYAN}Usage:${NC}"
echo -e "  ${DIM}judecode        Start interactive session${NC}"
echo -e "  ${DIM}judecode --help Show help${NC}"
echo -e "\n${YELLOW}Note:${NC}"
if [[ "$GLOBAL" == false ]]; then
    echo -e "  ${DIM}If 'judecode' is not found after installation,${NC}"
    echo -e "  ${DIM}restart your terminal or source your shell rc file:${NC}"
    echo -e "  ${DIM}  source ${RC_FILE:-\"~/.zshrc or ~/.bashrc\"}${NC}"
fi
