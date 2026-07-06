#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# Jude Code macOS build script
#
# Builds a self-contained .app bundle and packages it as a .dmg installer
# that the user can drag into /Applications — true one-click install.
#
# Usage:
#   ./scripts/build_mac_dmg.sh           # build .app + .dmg
#   ./scripts/build_mac_dmg.sh --app-only  # build just the .app
#
# Requirements (auto-installed into .venv if missing):
#   - PyInstaller
#   - PyObjC (for the native UI)
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

# ── Resolve project root ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ── Config ──
APP_NAME="JudeCode"
APP_BUNDLE="${APP_NAME}.app"
DMG_NAME="JudeCode-Installer.dmg"
BUILD_DIR="$PROJECT_ROOT/build"
DIST_DIR="$PROJECT_ROOT/dist"
DMG_DIR="$DIST_DIR/dmg-staging"
VENV_PY="$PROJECT_ROOT/.venv/bin/python"

# ── Colours ──
if [[ -t 1 ]]; then
    GREEN=$'\033[1;32m'
    CYAN=$'\033[1;36m'
    YELLOW=$'\033[1;33m'
    RED=$'\033[1;31m'
    RESET=$'\033[0m'
else
    GREEN=""; CYAN=""; YELLOW=""; RED=""; RESET=""
fi

log()  { echo "${CYAN}▶${RESET} $*"; }
ok()   { echo "${GREEN}✓${RESET} $*"; }
warn() { echo "${YELLOW}⚠${RESET} $*" >&2; }
die()  { echo "${RED}✗${RESET} $*" >&2; exit 1; }

# ── Preflight checks ──
[[ "$(uname)" == "Darwin" ]] || die "This script only runs on macOS."

if [[ ! -x "$VENV_PY" ]]; then
    log "Creating virtual environment (.venv)…"
    python3 -m venv .venv
    VENV_PY="$PROJECT_ROOT/.venv/bin/python"
fi

# Ensure required packages are installed in the venv.
ensure_pip_pkg() {
    local pkg="$1"
    if ! "$VENV_PY" -c "import $pkg" 2>/dev/null; then
        log "Installing $pkg into .venv…"
        "$VENV_PY" -m pip install --quiet --upgrade "$pkg"
    fi
}

ensure_pip_pkg "pyinstaller"
ensure_pip_pkg "objc"
ensure_pip_pkg "AppKit"
ensure_pip_pkg "rich"
ensure_pip_pkg "httpx"

ok "Environment ready ($("$VENV_PY" --version))"

# ── Clean previous build ──
log "Cleaning previous build artefacts…"
rm -rf "$BUILD_DIR" "$DIST_DIR"
mkdir -p "$DIST_DIR"

# ── Build the .app bundle ──
log "Building ${APP_BUNDLE} with PyInstaller…"
"$VENV_PY" -m PyInstaller \
    judecode_mac.spec \
    --noconfirm \
    --clean \
    --distpath "$DIST_DIR" \
    --workpath "$BUILD_DIR"

[[ -d "$DIST_DIR/$APP_BUNDLE" ]] || die "Build failed: $APP_BUNDLE not found in $DIST_DIR"
ok "Built $DIST_DIR/$APP_BUNDLE"

# Quick sanity check: the main executable should exist and be Mach-O.
MAIN_EXE="$DIST_DIR/$APP_BUNDLE/Contents/MacOS/$APP_NAME"
[[ -x "$MAIN_EXE" ]] || die "Main executable missing: $MAIN_EXE"
file "$MAIN_EXE" | grep -q "Mach-O" || die "Main executable is not a Mach-O binary."

# ── If --app-only, stop here ──
if [[ "${1:-}" == "--app-only" ]]; then
    ok "Done (app-only mode)."
    echo
    echo "  App: $DIST_DIR/$APP_BUNDLE"
    echo "  Open it with: open '$DIST_DIR/$APP_BUNDLE'"
    exit 0
fi

# ── Build the .dmg installer ──
log "Building .dmg installer…"
DMG_FINAL="$DIST_DIR/$DMG_NAME"
rm -rf "$DMG_DIR" "$DMG_FINAL"
mkdir -p "$DMG_DIR"

# Copy the .app into the staging dir.
cp -R "$DIST_DIR/$APP_BUNDLE" "$DMG_DIR/"

# Add a symlink to /Applications so users can drag the app there.
ln -sf /Applications "$DMG_DIR/Applications"

# Add a small README.
cat > "$DMG_DIR/README.txt" <<'EOF'
Jude Code — macOS installer
────────────────────────────

1. Drag the JudeCode icon onto the Applications folder.
2. Open JudeCode from Launchpad or /Applications.
3. On first launch, open Settings (⌘,) and paste your API key.

Supported providers:
  • DeepSeek  (https://platform.deepseek.com)
  • Anthropic (https://console.anthropic.com)
  • Z.AI / Zhipu GLM (https://z.ai)

Your API keys are stored locally at:
  ~/Library/Application Support/JudeCode/config.env
EOF

# Create the .dmg using hdiutil (no third-party tools required).
DMG_TMP="$DIST_DIR/${DMG_NAME%.dmg}-tmp.dmg"
log "Creating DMG image…"
hdiutil create \
    -volname "Jude Code" \
    -srcfolder "$DMG_DIR" \
    -fs HFS+ \
    -format UDRW \
    -imagekey zlib-level=9 \
    "$DMG_TMP" \
    >/dev/null

# Convert to a compressed, read-only DMG (UDZO).
log "Compressing DMG…"
hdiutil convert \
    "$DMG_TMP" \
    -format UDZO \
    -imagekey zlib-level=9 \
    -o "$DMG_FINAL" \
    >/dev/null

rm -f "$DMG_TMP"
rm -rf "$DMG_DIR"

[[ -f "$DMG_FINAL" ]] || die "DMG creation failed."

# Print final summary.
DMG_SIZE=$(du -h "$DMG_FINAL" | awk '{print $1}')
APP_SIZE=$(du -sh "$DIST_DIR/$APP_BUNDLE" | awk '{print $1}')

echo
ok "Build complete!"
echo
echo "  ${CYAN}App bundle:${RESET}  $DIST_DIR/$APP_BUNDLE  (${APP_SIZE})"
echo "  ${CYAN}Installer:${RESET}    $DMG_FINAL  (${DMG_SIZE})"
echo
echo "  Install: open '$DMG_FINAL'"
echo "  Test:    open '$DIST_DIR/$APP_BUNDLE'"
echo
