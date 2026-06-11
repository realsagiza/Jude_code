"""Configuration for Jude Code.

Multi-Provider Support:
    Jude Code supports multiple LLM providers. Set JUDECODE_PROVIDER to choose:
      - "deepseek"  : DeepSeek API (default, OpenAI-compatible)
      - "anthropic" : Anthropic Claude API (native Messages API)

    Provider-specific env vars:
      # DeepSeek
      JUDECODE_DEEPSEEK_API_KEY=...
      JUDECODE_DEEPSEEK_MODEL=deepseek-chat
      JUDECODE_DEEPSEEK_BASE_URL=https://api.deepseek.com

      # Anthropic
      JUDECODE_ANTHROPIC_API_KEY=...
      JUDECODE_ANTHROPIC_MODEL=claude-sonnet-4-20250514

    Other config values:
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
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
# ชี้ไปที่ .env ใน project root (2 level up จาก config.py)
# เพื่อให้เจอตัวที่ user แก้ไข ไม่ใช่ตัวใน package
_env_path = Path(__file__).parent.parent / '.env'
load_dotenv(_env_path, override=True)


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


# ── Provider Selection ──
# "deepseek" (default) or "anthropic"
PROVIDER = _env("PROVIDER", "deepseek").lower().strip()

# ── DeepSeek API (OpenAI-compatible) ──
# ใช้ DeepSeek API โดยตรง (ไม่ผ่าน Ollama)
# รองรับ deepseek-v4-flash (default, มี thinking mode) และ deepseek-v4-pro
# ⚠️ ต้องตั้งค่า JUDECODE_DEEPSEEK_API_KEY environment variable
DEEPSEEK_BASE_URL = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
_DEEPSEEK_KEY = _env("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_KEY = _DEEPSEEK_KEY
DEEPSEEK_MODEL = _env("DEEPSEEK_MODEL", "deepseek-chat")

# ── Anthropic API (Claude) ──
# ใช้ Anthropic Messages API โดยตรง (native protocol)
# ⚠️ ต้องตั้งค่า JUDECODE_ANTHROPIC_API_KEY environment variable
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = _env("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

# ── Active provider config (used by the app) ──
if PROVIDER == "anthropic":
    _ACTIVE_KEY = ANTHROPIC_API_KEY
    _ACTIVE_MODEL = ANTHROPIC_MODEL
else:
    _ACTIVE_KEY = DEEPSEEK_API_KEY
    _ACTIVE_MODEL = DEEPSEEK_MODEL

if not _ACTIVE_KEY:
    import warnings
    warnings.warn(
        f"⚠️  JUDECODE_{PROVIDER.upper()}_API_KEY ไม่ได้ตั้งค่า "
        f"กรุณาตั้ง environment variable ก่อนใช้งาน\n"
        f"   export JUDECODE_{PROVIDER.upper()}_API_KEY='your-key-here'",
        RuntimeWarning,
        stacklevel=2,
    )

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
# These reflect the active provider's settings
if PROVIDER == "anthropic":
    BASE_URL = "https://api.anthropic.com/v1"
    API_KEY = ANTHROPIC_API_KEY
    MODEL = ANTHROPIC_MODEL
else:
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

🚨 CRITICAL — Smart File Reading (Prevent Context Overflow):
The context window is limited to ~1M tokens. Reading huge files in one shot WILL crash.
Follow these rules STRICTLY:

1. CHECK SIZE FIRST — Before reading any file, estimate its size:
   - Use `wc -l <file>` (shell) to count lines
   - Or check file size via `ls -lh <file>`
   - Files > 300 lines are DANGEROUS to read in full

2. READ IN CHUNKS — For files > 200 lines, ALWAYS use offset + limit:
   - read(path="file.py", offset=1, limit=100)   ← first 100 lines
   - read(path="file.py", offset=101, limit=100)  ← next 100 lines
   - Never read more than 200-300 lines at once

3. SEARCH FIRST, READ SECOND — Before reading, narrow down:
   - Use grep to find relevant line numbers: `grep -n "function_name" file.py`
   - Use codebase_search to locate specific classes/functions
   - Then read ONLY the relevant line ranges

4. ASSEMBLE UNDERSTANDING INCREMENTALLY:
   - Read file structure first (first 50-80 lines = imports + class defs)
   - Then read specific sections as needed
   - Summarize what you've read before reading more
   - Build understanding across multiple small reads

5. USE SHELL FOR LARGE FILES — For very large files (>1000 lines):
   - Use `head -50`, `tail -50`, `sed -n '100,200p'` via shell instead of read tool
   - Shell output is more token-efficient than the read tool

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
