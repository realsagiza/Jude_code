"""Tool definitions and tool execution for Jude Code Agent."""

import json
from typing import Any

from judecode.utils.file_ops import read_file, write_file, edit_file, delete_file, list_directory
from judecode.utils.shell import execute_shell
from judecode.utils.search_tools import glob_search, grep_search
from judecode.utils.web_tools import web_fetch, web_search


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "Execute a shell command in the terminal. Use this to run commands, install packages, check git status, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read the contents of a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The file path to read"},
                    "offset": {"type": "integer", "description": "Line number to start from (1-indexed, default 1)"},
                    "limit": {"type": "integer", "description": "Maximum number of lines to read"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Write content to a file (creates or overwrites)",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The file path to write to"},
                    "content": {"type": "string", "description": "The content to write"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "Edit an existing file by replacing a specific string. The old_string must be unique in the file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The file path to edit"},
                    "old_string": {"type": "string", "description": "The exact text to replace (must be unique)"},
                    "new_string": {"type": "string", "description": "The replacement text"}
                },
                "required": ["path", "old_string", "new_string"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete",
            "description": "Delete a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The file path to delete"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Search for files matching a glob pattern",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern to search for. Use **/* for recursive search."},
                    "root": {"type": "string", "description": "Root directory to search in (default: current directory)"}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents using regex (uses ripgrep if available, falls back to Python regex)",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "path": {"type": "string", "description": "File or directory to search in (default: current directory)"},
                    "glob": {"type": "string", "description": "Glob filter for file names"}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch the content of a web page by URL",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to fetch"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web using DuckDuckGo",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                    "num_results": {"type": "integer", "description": "Number of results to return (default: 5)"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": "Use this tool to think through a complex problem step by step. This does not execute any action, just helps you reason.",
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {"type": "string", "description": "Your step-by-step reasoning"}
                },
                "required": ["thought"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ls",
            "description": "List the contents of a directory",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path (default: current directory)"}
                }
            }
        }
    }
]


def execute_tool(
    tool_name: str,
    tool_params: dict[str, Any],
) -> str:
    try:
        if tool_name == "shell":
            result = execute_shell(tool_params["command"])
            output = result["stdout"]
            if result["stderr"]:
                output += f"\n[stderr]: {result['stderr']}"
            output += f"\n[exit_code]: {result['exit_code']}"
            return output.strip()

        elif tool_name == "read":
            return read_file(
                tool_params["path"],
                offset=tool_params.get("offset", 1),
                limit=tool_params.get("limit"),
            )

        elif tool_name == "write":
            write_file(tool_params["path"], tool_params["content"])
            return f"File written successfully: {tool_params['path']}"

        elif tool_name == "edit":
            edit_file(
                tool_params["path"],
                tool_params["old_string"],
                tool_params["new_string"],
            )
            return f"File edited successfully: {tool_params['path']}"

        elif tool_name == "delete":
            delete_file(tool_params["path"])
            return f"File deleted: {tool_params['path']}"

        elif tool_name == "glob":
            return glob_search(
                tool_params["pattern"],
                tool_params.get("root", "."),
            )

        elif tool_name == "grep":
            return grep_search(
                tool_params["pattern"],
                tool_params.get("path", "."),
                glob=tool_params.get("glob"),
            )

        elif tool_name == "web_fetch":
            return web_fetch(tool_params["url"])

        elif tool_name == "web_search":
            return web_search(
                tool_params["query"],
                tool_params.get("num_results", 5),
            )

        elif tool_name == "think":
            return f"[Thought process]\n{tool_params['thought']}"

        elif tool_name == "ls":
            return list_directory(tool_params.get("path", "."))

        else:
            return f"Unknown tool: {tool_name}"

    except Exception as e:
        return f"Error executing tool '{tool_name}': {type(e).__name__}: {e}"
