"""Configuration for Jude Code.

Config values can be overridden via environment variables:
    JUDECODE_BASE_URL
    JUDECODE_API_KEY
    JUDECODE_MODEL
    JUDECODE_VISION_MODEL
    JUDECODE_MAX_TOKENS
    JUDECODE_TEMPERATURE
    JUDECODE_MAX_CONTINUATIONS
    JUDECODE_MAX_TURNS
    JUDECODE_VAULT

This allows the Windows .exe to be configured via config.ini
(loaded by runtime_hook_windows.py before this module is imported).
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def _env(key: str, default: str) -> str:
    """Get config from environment variable, with fallback to default."""
    env_key = f"JUDECODE_{key}"
    return os.environ.get(env_key, default)


def _env_int(key: str, default: int) -> int:
    """Get int config from environment variable."""
    try:
        return int(os.environ.get(f"JUDECODE_{key}", str(default)))
    except (ValueError, TypeError):
        return default


# ── DeepSeek API (Main Model) ──
# ใช้ DeepSeek API โดยตรง (ไม่ผ่าน Ollama)
# รองรับ deepseek-v4-flash (default, มี thinking mode) และ deepseek-v4-pro
# ⚠️ ต้องตั้งค่า JUDECODE_DEEPSEEK_API_KEY environment variable
DEEPSEEK_BASE_URL = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
_DEFAULT_KEY = _env("DEEPSEEK_API_KEY", "")
if not _DEFAULT_KEY:
    import warnings
    warnings.warn(
        "⚠️  JUDECODE_DEEPSEEK_API_KEY ไม่ได้ตั้งค่า กรุณาตั้ง environment variable ก่อนใช้งาน\n"
        "   export JUDECODE_DEEPSEEK_API_KEY='sk-your-key-here'",
        RuntimeWarning,
        stacklevel=2,
    )
DEEPSEEK_API_KEY = _DEFAULT_KEY
DEEPSEEK_MODEL = _env("DEEPSEEK_MODEL", "deepseek-chat")

# ── Vision API (Screenshot Analysis) ──
# รองรับทั้ง Ollama (local) และ Cloud API เช่น DashScope/Qwen, OpenAI Vision
# DeepSeek Cloud API ยังไม่รองรับ vision/multimodal mode
# ตั้งค่าผ่าน .env หรือ environment variables:
#   JUDECODE_VISION_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
#   JUDECODE_VISION_API_KEY=sk-your-key-here
#   JUDECODE_VISION_MODEL=qwen3.6-flash-2026-04-16
VISION_BASE_URL = _env("VISION_BASE_URL", "http://127.0.0.1:11434/v1")
VISION_API_KEY = _env("VISION_API_KEY", "ollama")
VISION_MODEL = _env("VISION_MODEL", "llava")

# ── Shared API Config (for backward compatibility) ──
BASE_URL = DEEPSEEK_BASE_URL
API_KEY = DEEPSEEK_API_KEY
MODEL = DEEPSEEK_MODEL
# deepseek-v4-flash output limit ~8K tokens
MAX_TOKENS = _env_int("MAX_TOKENS", 8192)
TEMPERATURE = float(_env("TEMPERATURE", "0.7"))

# ── Knowledge Vault ──
VAULT_PATH = _env("VAULT", os.path.expanduser("~/.judecode/vault"))

# ── Continuation / Nudge System ──
# Maximum number of auto-continuations before stopping
MAX_CONTINUATIONS = _env_int("MAX_CONTINUATIONS", 100)
# Maximum conversation turns before stopping
MAX_TURNS = _env_int("MAX_TURNS", 100)
# Whether to enable auto-continuation on stream errors
CONTINUE_ON_STREAM_ERROR = True
# Whether to enable auto-continuation when incomplete work is detected
# ปิดเป็น default เพราะ tool call errors + stream interruption ก็เพียงพอแล้ว
# incomplete_work pattern มัก false positive ในการสนทนาทั่วไป
CONTINUE_ON_INCOMPLETE_WORK = False
# Whether to enable auto-continuation on tool errors
CONTINUE_ON_TOOL_ERROR = True

SYSTEM_PROMPT = """You are Jude Code, an AI coding assistant that runs in the terminal.
You help users with software engineering tasks by writing code, running commands,
editing files, answering questions, and more.

Your tools (shell, read, write, edit, glob, grep, web, knowledge vault, task manager,
computer-use) are defined in the tool schema. Use them as needed.

Guidelines:
1. Think step by step before acting. Use the 'think' tool for complex reasoning.
2. Write clean, well-structured code and explain your reasoning briefly before changes.
3. Chain shell commands with && for sequential operations. Check errors after commands.
4. Use ripgrep (rg) for searching if available.
5. Test web development with curl or web_fetch tool.
6. Always handle errors gracefully.

Knowledge Vault (#tags and [[Wiki Links]]):
- Store important findings, decisions, and documentation.
- Check vault for relevant notes before starting complex work.
- Save a summary after completing significant work.

Codebase Indexing (project-wide understanding):
⚡ ALWAYS use codebase tools BEFORE reading files in a new project:
1. codebase_index (build)  - Scan project → save searchable index
2. codebase_summary        - Get overview: languages, dirs, largest files
3. codebase_search (query) - Find specific classes/functions/files by keyword
   → THEN use read tool ONLY on the specific files found

This saves HUGE tokens — instead of scanning blindly with glob/grep,
you search a pre-built index that's 5-10% the size of the full codebase.

Continuation System (auto-nudge):
If you see a [SYSTEM: ...] message, the system detected you stopped mid-task.
- Review what's done. If incomplete, continue. If done, confirm.
- Do NOT repeat work already done.
- For long files, write in chunks (append with edit, don't restart).

Pause / Interrupt:
User can pause you with Ctrl+C or /stop. When paused, wait for their next instruction.

Computer Use:
- Prefer get_browser_accessibility_snapshot() or get_desktop_accessibility_tree()
  over screenshot+vision (10-50x faster).
- Use screenshot(vision_model=...) only when accessibility trees are insufficient.
- Vision model (configured via JUDECODE_VISION_MODEL in .env) runs separately from your main model.

Task Manager:
Break down complex requests into tasks FIRST using task_add(), then
task_start() → work → task_complete() for each step.
Use task_next() / task_queue() / task_summary() to track progress.
"""
