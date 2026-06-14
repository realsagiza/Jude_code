"""Tool definitions and tool execution for Jude Code Agent."""

import json
from typing import Any

# ── Sentinel prefix for genuine tool execution errors ──
# Used so the continuation system can distinguish a REAL tool failure from
# normal tool output that merely *contains* the words "error executing tool"
# (e.g. when `read`/`grep` returns source code that includes that phrase).
TOOL_ERROR_PREFIX = "Error executing tool"

from judecode.utils.file_ops import read_file, write_file, edit_file, delete_file, list_directory
from judecode.utils.shell import execute_shell
from judecode.utils.search_tools import glob_search, grep_search
from judecode.utils.web_tools import web_fetch, web_search

# ── Task Management Tools ──
from judecode.utils.task_tools import execute_task_tool, TASK_TOOL_FUNCTIONS

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

# ── Codebase Indexer (Claude Code-style indexing) ──
from judecode.utils.codebase_indexer import (
    build_index,
    load_index,
    search_index,
    get_project_summary,
)

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
try:
    from judecode.utils.computer_tools import (
        screenshot, get_screen_size, get_mouse_position, get_active_window_info,
        mouse_move, mouse_click, mouse_double_click, mouse_drag,
        keyboard_type, keyboard_press, keyboard_hotkey,
        keyboard_type_enqueue, keyboard_queue_status, keyboard_queue_clear,
        scroll, open_app, list_running_apps,
        get_browser_accessibility_snapshot,
        get_desktop_accessibility_tree,
    )
except Exception:
    # If computer tools fail to import (e.g. headless server), define stubs
    def _stub_error(name):
        return lambda *a, **kw: f"Error: {name} is not available on this system (no GUI/display)."

    screenshot = _stub_error("screenshot")
    get_screen_size = _stub_error("get_screen_size")
    get_mouse_position = _stub_error("get_mouse_position")
    get_active_window_info = _stub_error("get_active_window_info")
    mouse_move = _stub_error("mouse_move")
    mouse_click = _stub_error("mouse_click")
    mouse_double_click = _stub_error("mouse_double_click")
    mouse_drag = _stub_error("mouse_drag")
    keyboard_type = _stub_error("keyboard_type")
    keyboard_press = _stub_error("keyboard_press")
    keyboard_hotkey = _stub_error("keyboard_hotkey")
    keyboard_type_enqueue = _stub_error("keyboard_type_enqueue")
    keyboard_queue_status = _stub_error("keyboard_queue_status")
    keyboard_queue_clear = _stub_error("keyboard_queue_clear")
    scroll = _stub_error("scroll")
    open_app = _stub_error("open_app")
    list_running_apps = _stub_error("list_running_apps")
    get_browser_accessibility_snapshot = _stub_error("get_browser_accessibility_snapshot")
    get_desktop_accessibility_tree = _stub_error("get_desktop_accessibility_tree")


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "Execute a shell command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read file. For >200 lines, use offset+limit. Check size with `wc -l` first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"},
                    "offset": {"type": "integer", "description": "Start line (1-indexed). Required if >200 lines."},
                    "limit": {"type": "integer", "description": "Max lines. Set 100-300 for large files. Required if >200 lines."}
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
                    "path": {"type": "string", "description": "File path"},
                    "content": {"type": "string", "description": "Content to write"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "Edit file by replacing a unique string.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "old_string": {"type": "string", "description": "Text to replace (must be unique)"},
                    "new_string": {"type": "string", "description": "Replacement text"}
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
                    "path": {"type": "string", "description": "File path"}
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
                    "pattern": {"type": "string", "description": "Glob pattern (**/* for recursive)"},
                    "root": {"type": "string", "description": "Root dir (default: .)"}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents by regex (ripgrep or Python fallback)",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern"},
                    "path": {"type": "string", "description": "Search path (default: .)"},
                    "glob": {"type": "string", "description": "Glob filter for filenames"}
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
                    "url": {"type": "string", "description": "URL"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web using Tavily AI Search (or DuckDuckGo fallback)",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "num_results": {"type": "integer", "description": "Num results (default: 5)"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": "Think through a problem step by step. No action executed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {"type": "string", "description": "Step-by-step reasoning"}
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
                    "path": {"type": "string", "description": "Dir path (default: .)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "vault_create_note",
            "description": "Create vault note (Obsidian-style).",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Note title (filename)"},
                    "content": {"type": "string", "description": "Markdown content"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags (without #)"},
                    "links": {"type": "array", "items": {"type": "string"}, "description": "Linked note titles"}
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
                    "title": {"type": "string", "description": "Note title"}
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "vault_update_note",
            "description": "Overwrite note content (keeps frontmatter).",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Note title"},
                    "content": {"type": "string", "description": "New markdown content"}
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
                    "title": {"type": "string", "description": "Note title"},
                    "content": {"type": "string", "description": "Content to append"}
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
                    "title": {"type": "string", "description": "Note title"}
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
            "description": "Search vault notes by title/content/tag. Returns ranked snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"}
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
            "description": "Build knowledge graph of notes, links, and tags.",
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
                    "title": {"type": "string", "description": "Note title"}
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
                    "tag": {"type": "string", "description": "Tag (without #)"}
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
            "description": "Read PDF text/tables. Use 'pages' for large PDFs, pdf_info first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pdf_path": {"type": "string", "description": "PDF path"},
                    "pages": {"type": "string", "description": "Page range, e.g. '1-3,5'. Use for PDFs >10 pages."}
                },
                "required": ["pdf_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pdf_info",
            "description": "Get PDF metadata (page count, size, author).",
            "parameters": {
                "type": "object",
                "properties": {
                    "pdf_path": {"type": "string", "description": "PDF path"}
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
            "description": "Read CSV as formatted table. Use max_rows for large files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "csv_path": {"type": "string", "description": "CSV path"},
                    "delimiter": {"type": "string", "description": "Field delimiter (default: ','). Use '\\t' for tab-separated."},
                    "has_header": {"type": "boolean", "description": "Has header (default: true)"},
                    "max_rows": {"type": "integer", "description": "Max rows (50-100 for large files)"}
                },
                "required": ["csv_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_excel",
            "description": "Read Excel (.xlsx) as table. Set max_rows for large files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "excel_path": {"type": "string", "description": "Excel path"},
                    "sheet_name": {"type": "string", "description": "Sheet name (default: first). Use list_excel_sheets first."},
                    "max_rows": {"type": "integer", "description": "Max rows (50-100 for large sheets)"}
                },
                "required": ["excel_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_csv",
            "description": "Write CSV. Data: rows by newlines, cells by commas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "csv_path": {"type": "string", "description": "Output CSV path"},
                    "data": {"type": "string", "description": "Data (rows by newlines, cells by commas)"},
                    "delimiter": {"type": "string", "description": "Delimiter (default: ',')"}
                },
                "required": ["csv_path", "data"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_excel",
            "description": "Write Excel (.xlsx). Data: rows by newlines, cells by tabs/pipes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "excel_path": {"type": "string", "description": "Output Excel path"},
                    "data": {"type": "string", "description": "Data (rows by newlines, cells by tabs/pipes)"},
                    "sheet_name": {"type": "string", "description": "Sheet name (default: 'Sheet1')"}
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
                    "excel_path": {"type": "string", "description": "Excel path"}
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
            "description": "Rename files by regex pattern. Use dry_run=True to preview.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory"},
                    "pattern": {"type": "string", "description": "Regex pattern for filenames"},
                    "replacement": {"type": "string", "description": "Replacement (use \\1, \\2 for groups)"},
                    "dry_run": {"type": "boolean", "description": "Preview only (default: true)"},
                    "recursive": {"type": "boolean", "description": "Recursive (default: false)"}
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
                    "source_dir": {"type": "string", "description": "Source dir"},
                    "dest_dir": {"type": "string", "description": "Dest dir"},
                    "pattern": {"type": "string", "description": "Glob filter (e.g. '*.pdf')"},
                    "recursive": {"type": "boolean", "description": "Recursive (default: false)"},
                    "dry_run": {"type": "boolean", "description": "Preview only (default: true)"}
                },
                "required": ["source_dir", "dest_dir"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "batch_delete",
            "description": "Delete files by glob pattern. Use dry_run=True to preview.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory"},
                    "pattern": {"type": "string", "description": "Glob pattern (e.g. '*.tmp')"},
                    "dry_run": {"type": "boolean", "description": "Preview only (default: true)"},
                    "recursive": {"type": "boolean", "description": "Recursive (default: false)"}
                },
                "required": ["directory", "pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "organize_by_extension",
            "description": "Organize files into folders by extension.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory"},
                    "dry_run": {"type": "boolean", "description": "Preview only (default: true)"},
                    "recursive": {"type": "boolean", "description": "Recursive (default: false)"}
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
                    "directory": {"type": "string", "description": "Directory"},
                    "by_content": {"type": "boolean", "description": "By content hash vs filename+size (default: false)"}
                },
                "required": ["directory"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "export_directory_tree",
            "description": "Export directory tree as text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory"},
                    "max_depth": {"type": "integer", "description": "Max depth (default: 3)"},
                    "show_size": {"type": "boolean", "description": "Show sizes (default: false)"}
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
                    "text": {"type": "string", "description": "Text"}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "wait_for",
            "description": "Wait N seconds. Useful between operations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {"type": "integer", "description": "Seconds (default: 1)"}
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
                    "directory": {"type": "string", "description": "Directory"},
                    "output_file": {"type": "string", "description": "Output path"},
                    "pattern": {"type": "string", "description": "Glob pattern (default: '*')"},
                    "separator": {"type": "string", "description": "Separator (default: '\n---\n')"},
                    "recursive": {"type": "boolean", "description": "Recursive (default: false)"}
                },
                "required": ["directory", "output_file"]
            }
        }
    },

    # ── ⚡ Codebase Indexer (Claude Code-style) ──
    {
        "type": "function",
        "function": {
            "name": "codebase_index",
            "description": "Build codebase index (classes, functions, imports). Use FIRST on new projects.",
            "parameters": {
                "type": "object",
                "properties": {
                    "root": {"type": "string", "description": "Root dir (default: .)"},
                    "force": {"type": "boolean", "description": "Force rebuild (default: false)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "codebase_search",
            "description": "Search codebase index by keyword. Faster than reading files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keywords to search"},
                    "root": {"type": "string", "description": "Root dir (default: .)"},
                    "max_results": {"type": "integer", "description": "Max results (default: 20)"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "codebase_summary",
            "description": "Project summary: files, languages, dirs, classes, functions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "root": {"type": "string", "description": "Root dir (default: .)"}
                }
            }
        }
    },

    # ── Computer Use Tools ──
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": "Take screenshot. Optional vision_model for analysis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "vision_model": {"type": "string", "description": "Vision model. Empty = screenshot only."},
                    "task_description": {"type": "string", "description": "Focus context."},
                    "save_path": {"type": "string", "description": "Save path"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_screen_size",
            "description": "Get screen resolution.",
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
            "description": "Get active window info (title, position, size).",
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
            "description": "Move mouse to screen coordinates (x, y). Use get_screen_size() first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X (0 = left)"},
                    "y": {"type": "integer", "description": "Y (0 = top)"},
                    "duration": {"type": "number", "description": "Duration sec (default: 0.5)"}
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
                    "button": {"type": "string", "description": "Button (default: left)"},
                    "x": {"type": "integer", "description": "X (optional)"},
                    "y": {"type": "integer", "description": "Y (optional)"}
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
                    "x": {"type": "integer", "description": "X (optional)"},
                    "y": {"type": "integer", "description": "Y (optional)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_drag",
            "description": "Drag mouse from one position to another.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_x": {"type": "integer", "description": "Start X"},
                    "start_y": {"type": "integer", "description": "Start Y"},
                    "end_x": {"type": "integer", "description": "End X"},
                    "end_y": {"type": "integer", "description": "End Y"},
                    "duration": {"type": "number", "description": "Duration sec (default: 0.5)"}
                },
                "required": ["start_x", "start_y", "end_x", "end_y"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "keyboard_type",
            "description": "Type text at cursor position.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to type"},
                    "interval": {"type": "number", "description": "Interval (default: 0.05)"}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "keyboard_press",
            "description": "Press a single key.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Key name"}
                },
                "required": ["key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "keyboard_hotkey",
            "description": "Press keyboard shortcut. E.g. 'command,c'=copy, 'alt,f4'=close.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keys": {"type": "string", "description": "Keys, comma-separated"}
                },
                "required": ["keys"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "keyboard_type_enqueue",
            "description": "Queue text for background typing (non-blocking, FIFO).",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to type"},
                    "interval": {"type": "number", "description": "Interval (default: 0.05)"}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "keyboard_queue_status",
            "description": "Check typing queue status and pending jobs.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "keyboard_queue_clear",
            "description": "Clear pending typing jobs (not current one).",
            "parameters": {
                "type": "object",
                "properties": {}
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
                    "clicks": {"type": "integer", "description": "Clicks (+up/-down)"},
                    "x": {"type": "integer", "description": "X (optional)"},
                    "y": {"type": "integer", "description": "Y (optional)"}
                },
                "required": ["clicks"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Open app by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "App name"}
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
            "description": "⚡ Browser accessibility tree. 10-50x faster than vision.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL (optional)"},
                    "task_description": {"type": "string", "description": "Focus (optional)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_desktop_accessibility_tree",
            "description": "⚡ Get desktop accessibility tree (macOS). 10-50x faster than vision.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_description": {"type": "string", "description": "Focus (optional)"}
                }
            }
        }
    },

    # ── ⚡ NEW: Task Management Tools ──
    {
        "type": "function",
        "function": {
            "name": "task_add",
            "description": "Add a new task to the task queue. Returns the task ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title"},
                    "description": {"type": "string", "description": "Description"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"], "description": "Priority (default: medium)"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags"}
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_list",
            "description": "List tasks with optional filters by status, priority, or tag.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["pending", "in_progress", "done", "cancelled"], "description": "Filter by status"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"], "description": "Filter by priority"},
                    "tag": {"type": "string", "description": "Filter by tag"},
                    "sort_by": {"type": "string", "enum": ["priority", "status", "created", "updated"], "description": "Sort by (default: priority)"},
                    "reverse": {"type": "boolean", "description": "Reverse sort"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_get",
            "description": "Get detailed info about a specific task by ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "Task ID"}
                },
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_update",
            "description": "Update a task's title, description, priority, or tags.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "Task ID"},
                    "title": {"type": "string", "description": "New title"},
                    "description": {"type": "string", "description": "New description"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"], "description": "New priority"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "New tags"}
                },
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_delete",
            "description": "Delete a task by ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "Task ID"}
                },
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_start",
            "description": "Mark a task as in_progress. Use this when you start working on a task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "Task ID"}
                },
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_complete",
            "description": "Mark a task as done. Call this when a task is completed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "Task ID"}
                },
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_cancel",
            "description": "Cancel a task (mark as cancelled).",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "Task ID"}
                },
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_next",
            "description": "Get the next pending task from the queue. Shows what to work on next.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_queue",
            "description": "Show the full prioritized task execution queue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["pending", "in_progress", "done", "cancelled"], "description": "Status (default: pending)"},
                    "sort_by": {"type": "string", "enum": ["priority", "status", "created", "updated"], "description": "Sort by (default: priority)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_advance",
            "description": "Complete the current task and advance to the next one in the queue.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_summary",
            "description": "Task statistics summary.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_clear_done",
            "description": "Delete all completed tasks from the list.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_reset_queue",
            "description": "Reset all in_progress tasks back to pending status.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_add_pomodoro",
            "description": "Add a pomodoro session count to a task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "Task ID"}
                },
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_import",
            "description": "Import tasks from a JSON file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "JSON path"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_export",
            "description": "Export all tasks to a JSON file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Output path"}
                },
                "required": ["path"]
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

        # ── ⚡ Codebase Indexer Tools ──
        elif tool_name == "codebase_index":
            result = build_index(
                root=tool_params.get("root", "."),
                force=tool_params.get("force", False),
            )
            if "error" in result:
                return f"Error building index: {result['error']}"
            stats = result.get("stats", {})
            return (
                f"✅ Codebase indexed successfully!\n"
                f"   Project: {result.get('project_name', 'unknown')}\n"
                f"   Files: {stats.get('total_files', 0)} | "
                f"Lines: {stats.get('total_lines', 0)} | "
                f"Classes: {stats.get('total_classes', 0)} | "
                f"Functions: {stats.get('total_functions', 0)}\n"
                f"   Languages: {', '.join(stats.get('languages', {}).keys())}\n"
                f"   Cache saved to: .judecode/codebase_index.json\n\n"
                f"💡 Now try `codebase_summary` for an overview or `codebase_search` to find specific code."
            )

        elif tool_name == "codebase_search":
            return search_index(
                query=tool_params["query"],
                root=tool_params.get("root", "."),
                max_results=tool_params.get("max_results", 20),
            )

        elif tool_name == "codebase_summary":
            return get_project_summary(root=tool_params.get("root", "."))

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

        elif tool_name == "keyboard_type_enqueue":
            return keyboard_type_enqueue(
                tool_params["text"],
                interval=tool_params.get("interval", 0.05),
            )

        elif tool_name == "keyboard_queue_status":
            return keyboard_queue_status()

        elif tool_name == "keyboard_queue_clear":
            return keyboard_queue_clear()

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

        # ── ⚡ NEW: Task Management Tools ──
        elif tool_name in TASK_TOOL_FUNCTIONS:
            return execute_task_tool(tool_name, tool_params)

        else:
            return f"Unknown tool: {tool_name}"

    except Exception as e:
        return f"Error executing tool '{tool_name}': {type(e).__name__}: {e}"
