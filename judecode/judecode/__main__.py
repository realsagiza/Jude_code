"""Entry point for `python -m judecode`."""

import sys
from judecode.ui.terminal import main_cli

if __name__ == "__main__":
    # Handle --version flag
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-V", "version"):
        try:
            from judecode import __version__
            print(f"Jude Code v{__version__}")
        except ImportError:
            print("Jude Code v0.1.0")
        sys.exit(0)
    
    main_cli()
