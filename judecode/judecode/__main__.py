"""Entry point for `python -m judecode`."""

import sys


def _running_as_mac_app() -> bool:
    """Detect whether we're running inside the frozen macOS .app bundle.

    We look for the ``JUDECODE_MAC_APP`` environment variable (set by the
    Mac launcher stub) OR the standard PyInstaller ``frozen`` attribute
    combined with a macOS platform.
    """
    if sys.platform != "darwin":
        return False
    if getattr(sys, "frozen", False):
        # Frozen by PyInstaller on macOS → use the native UI.
        return True
    return os.environ.get("JUDECODE_MAC_APP", "") == "1"


if __name__ == "__main__":
    import os

    # Handle --version flag
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-V", "version"):
        try:
            from judecode import __version__
            print(f"Jude Code v{__version__}")
        except ImportError:
            print("Jude Code v0.1.0")
        sys.exit(0)

    # ── Native macOS UI (when frozen as .app, or when explicitly requested) ──
    if _running_as_mac_app() or "--mac-ui" in sys.argv:
        from judecode.ui.mac_app import run_mac_app
        sys.exit(run_mac_app())

    # ── Default: terminal CLI ──
    from judecode.ui.terminal import main_cli
    main_cli()
