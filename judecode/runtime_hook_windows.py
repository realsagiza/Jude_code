"""
PyInstaller runtime hook for Jude Code on Windows.
This runs when the .exe starts up, before any judecode code runs.

It handles:
1. Loading config from config.ini (next to .exe)
2. Setting up the vault path
3. Ensuring ANSI support in Windows terminal
"""

import os
import sys
import platform


def _setup_windows_console():
    """Enable ANSI color support on Windows."""
    if platform.system() == 'Windows':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # Enable ENABLE_VIRTUAL_TERMINAL_PROCESSING (0x0004)
            STD_OUTPUT_HANDLE = -11
            h = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
            mode = ctypes.c_uint32()
            kernel32.GetConsoleMode(h, ctypes.byref(mode))
            kernel32.SetConsoleMode(h, mode.value | 0x0004)
        except Exception:
            pass  # Non-critical, colors just won't work


def _load_config_ini():
    """Load config from config.ini next to the executable."""
    try:
        import configparser

        # Find config.ini next to the .exe
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))

        config_path = os.path.join(base_dir, 'config.ini')
        if os.path.exists(config_path):
            config = configparser.ConfigParser()
            config.read(config_path)

            if 'JudeCode' in config:
                section = config['JudeCode']

                # ── DeepSeek API (Main Model) ──
                if 'DeepSeekBaseURL' in section:
                    os.environ['JUDECODE_DEEPSEEK_BASE_URL'] = section['DeepSeekBaseURL']
                if 'DeepSeekAPIKey' in section:
                    os.environ['JUDECODE_DEEPSEEK_API_KEY'] = section['DeepSeekAPIKey']
                if 'DeepSeekModel' in section:
                    os.environ['JUDECODE_DEEPSEEK_MODEL'] = section['DeepSeekModel']

                # ── Vision API ──
                if 'VisionBaseURL' in section:
                    os.environ['JUDECODE_VISION_BASE_URL'] = section['VisionBaseURL']
                if 'VisionModel' in section:
                    os.environ['JUDECODE_VISION_MODEL'] = section['VisionModel']

                # ── Backward compat ──
                if 'BaseURL' in section and 'DeepSeekBaseURL' not in section:
                    os.environ['JUDECODE_DEEPSEEK_BASE_URL'] = section['BaseURL']
                if 'Model' in section and 'DeepSeekModel' not in section:
                    os.environ['JUDECODE_DEEPSEEK_MODEL'] = section['Model']

                if 'VaultPath' in section:
                    os.environ['JUDECODE_VAULT'] = section['VaultPath']
    except Exception:
        pass  # Non-critical


# ── Run setup ──
_setup_windows_console()
_load_config_ini()
