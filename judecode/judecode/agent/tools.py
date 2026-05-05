"""Tool definitions and tool execution for Jude Code Agent."""

import json
from typing import Any

from judecode.utils.file_ops import read_file, write_file, edit_file, delete_file, list_directory
from judecode.utils.shell import execute_shell
from judecode.utils.search_tools import glob_search, grep_search
from judecode.utils.web_tools import web_fetch, web_search

from judecode.knowledge.notes import (
    create_note,
    read_note,
    update_note,
    append_to_note,
    delete_note,
    list_notes,
    get_backlinks,
)
from judecode.knowledge.search import (
    search_vault,
    build_knowledge_graph,
    get_related_notes,
    get_notes_by_tag,
)
from judecode.knowledge.vault import get_vault_structure

# ── Cowork-style tools ──
from judecode.utils.pdf_tools import read_pdf, pdf_info
from judecode.utils.spreadsheet_tools import (
    read_csv, read_excel, write_csv, write_excel, list_excel_sheets,
)
from judecode.utils.automation import (
    batch_rename, batch_copy, batch_delete, organize_by_extension,
    find_duplicates, export_directory_tree,
    clipboard_get, clipboard_set, wait_for, merge_text_files,
)

# ── Computer Use tools (vision + mouse + keyboard) ──
from judecode.utils.computer_tools import (
    screenshot, get_screen_size, get_mouse_position, get_active_window_info,
    mouse_move, mouse_click, mouse_double_click, mouse_drag,
    keyboard_type, keyboard_press, keyboard_hotkey,
    scroll, open_app, list_running_apps,
    # ⚡ NEW: Fast accessibility tree tools (10-50x faster than vision!)
    get_browser_accessibility_snapshot,
    get_desktop_accessibility_tree,
)


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
    },
    {
        "type": "function",
        "function": {
            "name": "vault_create_note",
            "description": "Create a new note in the knowledge vault (Obsidian-style). Use this to store important information, summaries, decisions, or documentation for future reference.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The title of the note (used as filename)"},
                    "content": {"type": "string", "description": "The markdown content of the note"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional list of tags (without #)"},
                    "links": {"type": "array", "items": {"type": "string"}, "description": "Optional list of linked note titles"}
                },
                "required": ["title", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "vault_read_note",
            "description": "Read the content of a note from the knowledge vault by its title.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The title of the note to read"}
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "vault_update_note",
            "description": "Overwrite a note's content (keeping frontmatter). Use this to update existing notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The title of the note to update"},
                    "content": {"type": "string", "description": "The new markdown content"}
                },
                "required": ["title", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "vault_append_note",
            "description": "Append content to an existing note in the vault.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The title of the note"},
                    "content": {"type": "string", "description": "The content to append"}
                },
                "required": ["title", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "vault_delete_note",
            "description": "Delete a note from the knowledge vault by its title.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The title of the note to delete"}
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "vault_list_notes",
            "description": "List all notes in the knowledge vault with their metadata (tags, links).",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "vault_search",
            "description": "Search notes in the vault by title, content, or tag. Returns ranked results with snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "vault_get_structure",
            "description": "Get the vault structure: path and list of all notes.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "vault_knowledge_graph",
            "description": "Build a knowledge graph of all notes, their links, and tags. Shows how notes are connected.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "vault_get_backlinks",
            "description": "Find all notes that link TO a specific note (backlinks).",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The title of the note to find backlinks for"}
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "vault_get_related",
            "description": "Find notes related to a given note by shared tags or direct links.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The title of the note"}
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "vault_get_by_tag",
            "description": "Get all notes that have a specific tag.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string", "description": "The tag to search for (without #)"}
                },
                "required": ["tag"]
            }
        }
    },

    # ── Cowork-style: PDF Tools ──
    {
        "type": "function",
        "function": {
            "name": "read_pdf",
            "description": "Read and extract text content from a PDF file. Supports both text-based PDFs and can extract tables.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pdf_path": {"type": "string", "description": "Path to the PDF file"},
                    "pages": {"type": "string", "description": "Optional page range, e.g. '1-3,5' (1-indexed). Leave empty for all pages."}
                },
                "required": ["pdf_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pdf_info",
            "description": "Get metadata and information about a PDF file (page count, size, author, etc.)",
            "parameters": {
                "type": "object",
                "properties": {
                    "pdf_path": {"type": "string", "description": "Path to the PDF file"}
                },
                "required": ["pdf_path"]
            }
        }
    },

    # ── Cowork-style: Spreadsheet Tools ──
    {
        "type": "function",
        "function": {
            "name": "read_csv",
            "description": "Read a CSV file and display as a formatted table.",
            "parameters": {
                "type": "object",
                "properties": {
                    "csv_path": {"type": "string", "description": "Path to CSV file"},
                    "delimiter": {"type": "string", "description": "Field delimiter (default: ','). Use '\\t' for tab-separated."},
                    "has_header": {"type": "boolean", "description": "Whether the first row is a header (default: true)"},
                    "max_rows": {"type": "integer", "description": "Maximum number of rows to read (default: all)"}
                },
                "required": ["csv_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_excel",
            "description": "Read an Excel (.xlsx) file and display as a formatted table.",
            "parameters": {
                "type": "object",
                "properties": {
                    "excel_path": {"type": "string", "description": "Path to .xlsx file"},
                    "sheet_name": {"type": "string", "description": "Specific sheet to read (default: first sheet)"},
                    "max_rows": {"type": "integer", "description": "Maximum rows to read per sheet (default: all)"}
                },
                "required": ["excel_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_csv",
            "description": "Write data to a CSV file. Provide data as text with rows separated by newlines and cells by commas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "csv_path": {"type": "string", "description": "Path to write CSV file"},
                    "data": {"type": "string", "description": "Data as text (rows separated by newlines, cells by commas)"},
                    "delimiter": {"type": "string", "description": "Field delimiter (default: ',')"}
                },
                "required": ["csv_path", "data"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_excel",
            "description": "Write data to an Excel (.xlsx) file. Provide data as text with rows by newlines, cells by tabs or pipes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "excel_path": {"type": "string", "description": "Path to write .xlsx file"},
                    "data": {"type": "string", "description": "Data as text (rows by newlines, cells by tabs or pipes)"},
                    "sheet_name": {"type": "string", "description": "Name of the sheet (default: 'Sheet1')"}
                },
                "required": ["excel_path", "data"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_excel_sheets",
            "description": "List all sheet names in an Excel (.xlsx) file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "excel_path": {"type": "string", "description": "Path to .xlsx file"}
                },
                "required": ["excel_path"]
            }
        }
    },

    # ── Cowork-style: Automation Tools ──
    {
        "type": "function",
        "function": {
            "name": "batch_rename",
            "description": "Rename multiple files matching a regex pattern. Use dry_run=True first to preview changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory to operate in"},
                    "pattern": {"type": "string", "description": "Regex pattern to match in filenames (e.g. 'screenshot_(\\\\d+)' to match 'screenshot_01')"},
                    "replacement": {"type": "string", "description": "Replacement string (use \\\\1, \\\\2 for captured groups)"},
                    "dry_run": {"type": "boolean", "description": "If True, only preview what would be renamed (default: true)"},
                    "recursive": {"type": "boolean", "description": "If True, search subdirectories (default: false)"}
                },
                "required": ["directory", "pattern", "replacement"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "batch_copy",
            "description": "Copy multiple files matching a pattern from source to destination.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_dir": {"type": "string", "description": "Source directory"},
                    "dest_dir": {"type": "string", "description": "Destination directory"},
                    "pattern": {"type": "string", "description": "Optional glob pattern to filter files (e.g. '*.pdf')"},
                    "recursive": {"type": "boolean", "description": "If True, search subdirectories (default: false)"},
                    "dry_run": {"type": "boolean", "description": "If True, only preview (default: true)"}
                },
                "required": ["source_dir", "dest_dir"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "batch_delete",
            "description": "Delete multiple files matching a glob pattern. Use dry_run=True first to preview.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory to operate in"},
                    "pattern": {"type": "string", "description": "Glob pattern (e.g. '*.tmp', '*.log')"},
                    "dry_run": {"type": "boolean", "description": "If True, only preview what would be deleted (default: true)"},
                    "recursive": {"type": "boolean", "description": "If True, search subdirectories (default: false)"}
                },
                "required": ["directory", "pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "organize_by_extension",
            "description": "Organize files into folders by their file extension (e.g. .pdf -> PDF/ folder).",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory to organize"},
                    "dry_run": {"type": "boolean", "description": "If True, only preview (default: true)"},
                    "recursive": {"type": "boolean", "description": "If True, also organize files in subdirectories (default: false)"}
                },
                "required": ["directory"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_duplicates",
            "description": "Find duplicate files in a directory by name+size or by content hash.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory to search"},
                    "by_content": {"type": "boolean", "description": "If True, compare by MD5 hash (slower but accurate). If False, compare by filename+size (default: false)"}
                },
                "required": ["directory"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "export_directory_tree",
            "description": "Export the directory tree structure as formatted text. Useful for understanding project structure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory to scan"},
                    "max_depth": {"type": "integer", "description": "Maximum depth to traverse (default: 3)"},
                    "show_size": {"type": "boolean", "description": "If True, show file sizes (default: false)"}
                },
                "required": ["directory"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "clipboard_get",
            "description": "Get text content from the system clipboard.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "clipboard_set",
            "description": "Copy text to the system clipboard.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to copy to clipboard"}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "wait_for",
            "description": "Wait/pause for a specified number of seconds. Useful in multi-step workflows between operations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {"type": "integer", "description": "Number of seconds to wait (default: 1)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "merge_text_files",
            "description": "Merge multiple text files into a single output file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory containing files to merge"},
                    "output_file": {"type": "string", "description": "Output file path"},
                    "pattern": {"type": "string", "description": "Glob pattern to match files (default: '*')"},
                    "separator": {"type": "string", "description": "Separator between file contents (default: '\\n\\n---\\n\\n')"},
                    "recursive": {"type": "boolean", "description": "If True, search subdirectories (default: false)"}
                },
                "required": ["directory", "output_file"]
            }
        }
    },

    # ── Computer Use Tools ──
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": "Take a screenshot of the current screen. Optionally analyze it with a vision model (qwen3.5:397b-cloud) to understand what's on screen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "vision_model": {"type": "string", "description": "Vision model name to analyze the screenshot (e.g. 'qwen3.5:397b-cloud'). Leave empty to just take screenshot without analysis."},
                    "task_description": {"type": "string", "description": "Optional context about what the user wants to do, to focus the vision analysis."},
                    "save_path": {"type": "string", "description": "Optional path to save the screenshot file."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_screen_size",
            "description": "Get the screen resolution (width x height). Useful before moving the mouse.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_mouse_position",
            "description": "Get the current mouse cursor position (x, y coordinates).",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_active_window_info",
            "description": "Get information about the currently active window (title, position, size).",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_move",
            "description": "Move the mouse cursor to absolute screen coordinates (x, y). Use get_screen_size() first to know the bounds.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X coordinate (0 = left edge of screen)"},
                    "y": {"type": "integer", "description": "Y coordinate (0 = top edge of screen)"},
                    "duration": {"type": "number", "description": "Seconds to animate the movement (default: 0.5)"}
                },
                "required": ["x", "y"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_click",
            "description": "Click the mouse at current position or specified coordinates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "button": {"type": "string", "description": "Mouse button: 'left', 'right', or 'middle' (default: 'left')"},
                    "x": {"type": "integer", "description": "Optional X coordinate to move to before clicking"},
                    "y": {"type": "integer", "description": "Optional Y coordinate to move to before clicking"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_double_click",
            "description": "Double-click at current position or specified coordinates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "Optional X coordinate"},
                    "y": {"type": "integer", "description": "Optional Y coordinate"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_drag",
            "description": "Drag the mouse from one position to another (click and hold while moving).",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_x": {"type": "integer", "description": "Starting X coordinate"},
                    "start_y": {"type": "integer", "description": "Starting Y coordinate"},
                    "end_x": {"type": "integer", "description": "Ending X coordinate"},
                    "end_y": {"type": "integer", "description": "Ending Y coordinate"},
                    "duration": {"type": "number", "description": "Duration of the drag in seconds (default: 0.5)"}
                },
                "required": ["start_x", "start_y", "end_x", "end_y"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "keyboard_type",
            "description": "Type text at the current cursor position. Useful for filling forms, search bars, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The text to type"},
                    "interval": {"type": "number", "description": "Seconds between each key press (default: 0.05)"}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "keyboard_press",
            "description": "Press a single key (e.g., 'enter', 'tab', 'escape', 'space', 'backspace', 'delete').",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Key name: enter, tab, escape, space, backspace, delete, up, down, left, right, home, end, pageup, pagedown, etc."}
                },
                "required": ["key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "keyboard_hotkey",
            "description": "Press a keyboard shortcut combination. Examples: 'command,c' for copy, 'command,v' for paste, 'command,tab' for app switch, 'alt,f4' for close window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keys": {"type": "string", "description": "Comma-separated key names (e.g. 'command,c' for copy, 'command,shift,3' for screenshot)"}
                },
                "required": ["keys"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "Scroll the mouse wheel. Positive = scroll up, Negative = scroll down.",
            "parameters": {
                "type": "object",
                "properties": {
                    "clicks": {"type": "integer", "description": "Number of scroll clicks. Positive = scroll up, Negative = scroll down."},
                    "x": {"type": "integer", "description": "Optional X position to scroll at"},
                    "y": {"type": "integer", "description": "Optional Y position to scroll at"}
                },
                "required": ["clicks"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Open an application by name (e.g., 'Safari', 'Chrome', 'Terminal', 'Finder', 'Notes').",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "Application name (e.g., 'Safari', 'Google Chrome', 'Terminal', 'Finder', 'Notes')"}
                },
                "required": ["app_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_running_apps",
            "description": "List currently running applications with visible windows.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },

    # ── ⚡ NEW: Fast Accessibility Tree Tools (10-50x faster than vision) ──
    {
        "type": "function",
        "function": {
            "name": "get_browser_accessibility_snapshot",
            "description": "⚡ FAST: Get a structured accessibility tree of the current browser page instead of using a vision model. Returns text with element labels, roles, and ref IDs. The LLM can read this directly to decide what to click/type. 10-50x faster than screenshot+vision. No vision model needed! Requires Playwright (pip install playwright && playwright install chromium).",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Optional URL to navigate to first before getting the accessibility tree."},
                    "task_description": {"type": "string", "description": "Optional context about what the user wants to do, to focus the analysis."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_desktop_accessibility_tree",
            "description": "⚡ FAST: Get a structured accessibility tree of the current active desktop window (macOS only). Uses the macOS Accessibility API (AXUIElement) to get UI element info. 10-50x faster than screenshot+vision. No vision model needed! Returns app name, window info, and UI elements in the active window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_description": {"type": "string", "description": "Optional context about what the user wants to do, to focus the analysis."}
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

        # ── Knowledge Vault Tools ──
        elif tool_name == "vault_create_note":
            path = create_note(
                title=tool_params["title"],
                content=tool_params.get("content", ""),
                tags=tool_params.get("tags"),
                links=tool_params.get("links"),
            )
            return f"Note created: {path}"

        elif tool_name == "vault_read_note":
            return read_note(tool_params["title"])

        elif tool_name == "vault_update_note":
            return update_note(tool_params["title"], tool_params["content"])

        elif tool_name == "vault_append_note":
            return append_to_note(tool_params["title"], tool_params["content"])

        elif tool_name == "vault_delete_note":
            return delete_note(tool_params["title"])

        elif tool_name == "vault_list_notes":
            notes = list_notes()
            return json.dumps(notes, ensure_ascii=False, indent=2)

        elif tool_name == "vault_search":
            results = search_vault(tool_params["query"])
            return json.dumps(results, ensure_ascii=False, indent=2)

        elif tool_name == "vault_get_structure":
            return json.dumps(get_vault_structure(), ensure_ascii=False, indent=2)

        elif tool_name == "vault_knowledge_graph":
            return json.dumps(build_knowledge_graph(), ensure_ascii=False, indent=2)

        elif tool_name == "vault_get_backlinks":
            links = get_backlinks(tool_params["title"])
            return json.dumps(links, ensure_ascii=False, indent=2)

        elif tool_name == "vault_get_related":
            related = get_related_notes(tool_params["title"])
            return json.dumps(related, ensure_ascii=False, indent=2)

        elif tool_name == "vault_get_by_tag":
            notes = get_notes_by_tag(tool_params["tag"])
            return json.dumps(notes, ensure_ascii=False, indent=2)

        # ── Cowork-style: PDF Tools ──
        elif tool_name == "read_pdf":
            return read_pdf(
                tool_params["pdf_path"],
                pages=tool_params.get("pages"),
            )

        elif tool_name == "pdf_info":
            return pdf_info(tool_params["pdf_path"])

        # ── Cowork-style: Spreadsheet Tools ──
        elif tool_name == "read_csv":
            return read_csv(
                tool_params["csv_path"],
                delimiter=tool_params.get("delimiter", ","),
                has_header=tool_params.get("has_header", True),
                max_rows=tool_params.get("max_rows"),
            )

        elif tool_name == "read_excel":
            return read_excel(
                tool_params["excel_path"],
                sheet_name=tool_params.get("sheet_name"),
                max_rows=tool_params.get("max_rows"),
            )

        elif tool_name == "write_csv":
            return write_csv(
                tool_params["csv_path"],
                tool_params["data"],
                delimiter=tool_params.get("delimiter", ","),
            )

        elif tool_name == "write_excel":
            return write_excel(
                tool_params["excel_path"],
                tool_params["data"],
                sheet_name=tool_params.get("sheet_name", "Sheet1"),
            )

        elif tool_name == "list_excel_sheets":
            return list_excel_sheets(tool_params["excel_path"])

        # ── Cowork-style: Automation Tools ──
        elif tool_name == "batch_rename":
            return batch_rename(
                tool_params["directory"],
                tool_params["pattern"],
                tool_params["replacement"],
                dry_run=tool_params.get("dry_run", True),
                recursive=tool_params.get("recursive", False),
            )

        elif tool_name == "batch_copy":
            return batch_copy(
                tool_params["source_dir"],
                tool_params["dest_dir"],
                pattern=tool_params.get("pattern"),
                recursive=tool_params.get("recursive", False),
                dry_run=tool_params.get("dry_run", True),
            )

        elif tool_name == "batch_delete":
            return batch_delete(
                tool_params["directory"],
                tool_params["pattern"],
                dry_run=tool_params.get("dry_run", True),
                recursive=tool_params.get("recursive", False),
            )

        elif tool_name == "organize_by_extension":
            return organize_by_extension(
                tool_params["directory"],
                dry_run=tool_params.get("dry_run", True),
                recursive=tool_params.get("recursive", False),
            )

        elif tool_name == "find_duplicates":
            return find_duplicates(
                tool_params["directory"],
                by_content=tool_params.get("by_content", False),
            )

        elif tool_name == "export_directory_tree":
            return export_directory_tree(
                tool_params["directory"],
                max_depth=tool_params.get("max_depth", 3),
                show_size=tool_params.get("show_size", False),
            )

        elif tool_name == "clipboard_get":
            return clipboard_get()

        elif tool_name == "clipboard_set":
            return clipboard_set(tool_params["text"])

        elif tool_name == "wait_for":
            return wait_for(seconds=tool_params.get("seconds", 1))

        elif tool_name == "merge_text_files":
            return merge_text_files(
                tool_params["directory"],
                tool_params["output_file"],
                pattern=tool_params.get("pattern", "*"),
                separator=tool_params.get("separator", "\n\n---\n\n"),
                recursive=tool_params.get("recursive", False),
            )

        # ── Computer Use Tools ──
        elif tool_name == "screenshot":
            return screenshot(
                vision_model=tool_params.get("vision_model"),
                task_description=tool_params.get("task_description"),
                save_path=tool_params.get("save_path"),
            )

        elif tool_name == "get_screen_size":
            return get_screen_size()

        elif tool_name == "get_mouse_position":
            return get_mouse_position()

        elif tool_name == "get_active_window_info":
            return get_active_window_info()

        elif tool_name == "mouse_move":
            return mouse_move(
                tool_params["x"],
                tool_params["y"],
                duration=tool_params.get("duration", 0.5),
            )

        elif tool_name == "mouse_click":
            return mouse_click(
                button=tool_params.get("button", "left"),
                x=tool_params.get("x"),
                y=tool_params.get("y"),
            )

        elif tool_name == "mouse_double_click":
            return mouse_double_click(
                x=tool_params.get("x"),
                y=tool_params.get("y"),
            )

        elif tool_name == "mouse_drag":
            return mouse_drag(
                tool_params["start_x"],
                tool_params["start_y"],
                tool_params["end_x"],
                tool_params["end_y"],
                duration=tool_params.get("duration", 0.5),
            )

        elif tool_name == "keyboard_type":
            return keyboard_type(
                tool_params["text"],
                interval=tool_params.get("interval", 0.05),
            )

        elif tool_name == "keyboard_press":
            return keyboard_press(tool_params["key"])

        elif tool_name == "keyboard_hotkey":
            keys_str = tool_params["keys"]
            keys = [k.strip() for k in keys_str.split(",")]
            return keyboard_hotkey(*keys)

        elif tool_name == "scroll":
            return scroll(
                tool_params["clicks"],
                x=tool_params.get("x"),
                y=tool_params.get("y"),
            )

        elif tool_name == "open_app":
            return open_app(tool_params["app_name"])

        elif tool_name == "list_running_apps":
            return list_running_apps()

        # ── ⚡ NEW: Fast Accessibility Tree Tools ──
        elif tool_name == "get_browser_accessibility_snapshot":
            return get_browser_accessibility_snapshot(
                url=tool_params.get("url"),
                task_description=tool_params.get("task_description"),
            )

        elif tool_name == "get_desktop_accessibility_tree":
            return get_desktop_accessibility_tree(
                task_description=tool_params.get("task_description"),
            )

        else:
            return f"Unknown tool: {tool_name}"

    except Exception as e:
        return f"Error executing tool '{tool_name}': {type(e).__name__}: {e}"
