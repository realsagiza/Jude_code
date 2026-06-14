"""Configuration for Jude Code.

Multi-Provider Support:
    Jude Code supports multiple LLM providers. Set JUDECODE_PROVIDER to choose:
      - "deepseek"  : DeepSeek API (default, OpenAI-compatible)
      - "anthropic" : Anthropic Claude API (native Messages API)
      - "zai"       : Z.AI / Zhipu GLM API (OpenAI-compatible)

    Provider-specific env vars:
      # DeepSeek
      JUDECODE_DEEPSEEK_API_KEY=...
      JUDECODE_DEEPSEEK_MODEL=deepseek-chat
      JUDECODE_DEEPSEEK_BASE_URL=https://api.deepseek.com

      # Anthropic
      JUDECODE_ANTHROPIC_API_KEY=...
      JUDECODE_ANTHROPIC_MODEL=claude-sonnet-4-20250514

      # Z.AI (GLM)
      JUDECODE_ZAI_API_KEY=...
      JUDECODE_ZAI_MODEL=glm-4.6
      JUDECODE_ZAI_BASE_URL=https://api.z.ai/api/paas/v4
      # For GLM Coding Plan use: https://api.z.ai/api/coding/paas/v4

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
# "deepseek" (default), "anthropic", or "zai"
PROVIDER = _env("PROVIDER", "deepseek").lower().strip()

# ── DeepSeek API (OpenAI-compatible) ──
# ใช้ DeepSeek Cloud API โดยตรง
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

# ── Z.AI / Zhipu GLM API (OpenAI-compatible) ──
# ใช้ Z.AI GLM models (glm-4.6 ฯลฯ) ผ่าน OpenAI-compatible endpoint
# ⚠️ ต้องตั้งค่า JUDECODE_ZAI_API_KEY environment variable
# General endpoint:      https://api.z.ai/api/paas/v4
# GLM Coding Plan:       https://api.z.ai/api/coding/paas/v4
ZAI_BASE_URL = _env("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4")
ZAI_API_KEY = _env("ZAI_API_KEY", "")
ZAI_MODEL = _env("ZAI_MODEL", "glm-4.6")

# ── Active provider config (used by the app) ──
if PROVIDER == "anthropic":
    _ACTIVE_KEY = ANTHROPIC_API_KEY
    _ACTIVE_MODEL = ANTHROPIC_MODEL
elif PROVIDER == "zai":
    _ACTIVE_KEY = ZAI_API_KEY
    _ACTIVE_MODEL = ZAI_MODEL
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
# รองรับ Cloud API เช่น DashScope/Qwen, OpenAI Vision
# DeepSeek Cloud API ยังไม่รองรับ vision/multimodal mode
# ⚠️ ต้องตั้งค่าผ่าน .env ทั้ง 3 ตัวแปร ไม่มี default fallback:
#   JUDECODE_VISION_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
#   JUDECODE_VISION_API_KEY=sk-your-key-here
#   JUDECODE_VISION_MODEL=qwen-vl-max
#
# Example with DashScope (Alibaba Cloud Qwen):
#   JUDECODE_VISION_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
#   JUDECODE_VISION_API_KEY=sk-your-dashscope-key-here
#   JUDECODE_VISION_MODEL=qwen-vl-max
#
# Example with OpenAI:
#   JUDECODE_VISION_BASE_URL=https://api.openai.com/v1
#   JUDECODE_VISION_API_KEY=sk-your-openai-key-here
#   JUDECODE_VISION_MODEL=gpt-4o-mini
VISION_BASE_URL = _env("VISION_BASE_URL", "")
VISION_API_KEY = _env("VISION_API_KEY", "")
VISION_MODEL = _env("VISION_MODEL", "")

# ── Shared API Config (for backward compatibility) ──
# These reflect the active provider's settings
if PROVIDER == "anthropic":
    BASE_URL = "https://api.anthropic.com/v1"
    API_KEY = ANTHROPIC_API_KEY
    MODEL = ANTHROPIC_MODEL
elif PROVIDER == "zai":
    BASE_URL = ZAI_BASE_URL
    API_KEY = ZAI_API_KEY
    MODEL = ZAI_MODEL
else:
    BASE_URL = DEEPSEEK_BASE_URL
    API_KEY = DEEPSEEK_API_KEY
    MODEL = DEEPSEEK_MODEL
# deepseek-v4-flash output limit ~8K tokens
MAX_TOKENS = _env_int("MAX_TOKENS", 8192)
TEMPERATURE = float(_env("TEMPERATURE", "0.7"))

# ── Telegram Bot ──
# ตั้งค่าเพื่อให้ Jude Code รับข้อความและตอบกลับผ่าน Telegram
# 1. สร้าง bot กับ @BotFather แล้วเอา token มาใส่
# 2. เอา Telegram User ID ของคุณใส่ใน TELEGRAM_ALLOWED_USERS (คั่นด้วย ,)
# ดู user_id ได้จาก @userinfobot
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_USERS = _env("TELEGRAM_ALLOWED_USERS", "")
TELEGRAM_MAX_MESSAGE_LENGTH = _env_int("TELEGRAM_MAX_MESSAGE_LENGTH", 4000)

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

SYSTEM_PROMPT = """You are Jude Code, an AI coding assistant in the terminal.
Help users with software engineering: write code, run commands, edit files, answer questions.

Guidelines:
1. Think step by step. Use 'think' tool for complex reasoning.
2. Write clean code. Explain briefly before changes.
3. Chain shell commands with &&. Check errors after each.
4. Use ripgrep (rg) for searching if available.
5. Handle errors gracefully.

Key rules:
- ALWAYS use codebase_index/search BEFORE reading files in a new project (saves huge tokens).
- For files >200 lines: use `wc -l` first, then read with offset+limit (never read huge files at once).
- Knowledge Vault: check vault before complex work; save summaries after.
- [SYSTEM: ...] messages = auto-nudge. Continue if incomplete, confirm if done. Don't repeat work.
- User can pause with Ctrl+C or /stop.
- Prefer accessibility tree tools over screenshot+vision (10-50x faster).
- Break complex requests into tasks with task_add() → task_start() → task_complete().
"""
