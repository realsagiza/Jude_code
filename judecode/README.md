# Jude Code

Your AI coding assistant that runs in the terminal — inspired by Claude Code but powered by your own API.

## Overview

Jude Code is a terminal-based CLI application that provides an agentic coding experience. It connects to a Cloud AI API (DeepSeek, Anthropic, or Z.AI/GLM) and provides tools like shell execution, file operations, web search, and more.

## Installation

### macOS (Homebrew Python 3.12+)

Homebrew marks Python as "externally managed" (PEP 668).
The installer will **automatically create a `.venv`** and install there.

```bash
cd judecode
chmod +x scripts/install.sh
./scripts/install.sh        # Safe user install (uses venv automatically)
```

If you prefer a system install (not recommended):

```bash
./scripts/install.sh --global --break-system-packages
```

### Windows

```powershell
# Clone the repository
git clone <repo-url>
cd judecode

# Run the install script
.\scripts\install.bat        # User install (no admin needed)
.\scripts\install.bat /global # System-wide install (requires admin)
```

### Manual (virtual environment)

```bash
python3 -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
pip install -e .
```

After installation, the `judecode` command will be available system-wide.

## Usage

Simply run in your terminal:

```bash
judecode
```

You'll see a cool greeting and can start chatting with the AI. The agent can:

- Run shell commands (`shell`)
- Read files (`read`)
- Write/create files (`write`)
- Edit existing files (`edit`)
- Delete files (`delete`)
- Search files by pattern (`glob`)
- Search file contents with regex (`grep`)
- Fetch web pages (`web_fetch`)
- Search the web (`web_search`)
- Think through complex problems (`think`)
- List directory contents (`ls`)

### Commands

While in the CLI:

| Command | Description |
|---------|-------------|
| `/help` | Show help |
| `/quit`, `Ctrl+D` | Exit |
| `/clear` | Reset conversation history |
| `/model` | Show current model info |

## Configuration

Jude Code connects to a Cloud AI API (DeepSeek, Anthropic, or Z.AI/GLM):

```python
BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-chat"
API_KEY = "your-api-key-here"
```

These values are configured in `judecode/config.py` and `.env`.

## Project Structure

```
judecode/
judecode/
judecode/┌──── judecode/
│   ┌──── api/         # API client
│   │       client.py
│   ┌──── agent/         # Agent engine + tools
│   │       engine.py
│   │       tools.py
│   ┌──── ui/            # Terminal UI
│   │       terminal.py
│   ┌──── utils/         # File ops, shell, search, web tools
│   │       file_ops.py
│   │       shell.py
│   │       search_tools.py
│   │       web_tools.py
│   config.py
│   __main__.py
tests/          # Unit tests
test_file_ops.py
test_shell.py
requirements.txt
setup.py
pyproject.toml
```

## API Behavior

The API client uses OpenAI-compatible tool calls:
- `model`: configured via .env (e.g., deepseek-chat, GLM-5.1)
- `messages`: full conversation history (system + user + assistant + tool)
- `tools`: function definitions for the agent
- `stream`: enabled (SSE streaming)

When the model returns tool calls, the agent:
1. Appends the tool_call to the conversation
2. Executes each tool and records results
3. Sends the tool results back to the model
4. Continues until the model provides a final response

## Running Tests

```bash
pytest tests/ -v
```

## License

MIT
