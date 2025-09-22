# tools/parsing_utils.py
from __future__ import annotations

import os
import re

# ---- Regular Expression Patterns for Parsing Dumps ----
# Finds any potential path-like string. Used to extract paths from headings.
# e.g., "Google provider — packages/providers/src/google.ts" -> "packages/providers/src/google.ts"
HEADING_PATH_EXTRACT_RE = re.compile(
    r"\b(?P<path>[a-zA-Z0-9._\-]+\/[a-zA-Z0-9._\-\/]+\.[a-zA-Z]{2,})\b"
)
# Finds simple filenames in headings, e.g. "Root package.json" -> "package.json"
HEADING_FILENAME_EXTRACT_RE = re.compile(
    r"\b(?P<filename>[a-zA-Z0-9._\-]+\.[a-zA-Z]{2,})\b"
)
# Matches a Markdown code fence start and captures its language hint
FENCE_RE = re.compile(
    r"^\s*(?P<fence>```+|~~~+)(?P<lang>[a-zA-Z0-9]*)\s*$", re.MULTILINE
)
# Matches an ASCII tree line to determine indent and name
TREE_LINE_RE = re.compile(r"^(?P<indent>[│\s]*)(?:[├└]──\s?)(?P<name>.*)$")

# ---- File Type Heuristics ----
WHITELIST_BARE = {
    "Dockerfile", "README.md", "README", ".env", ".env.sample", ".env.example",
    "docker-compose.yml", "requirements.txt", "pyproject.toml", ".gitignore", "tsconfig.base.json",
}
TEXTY_EXTS = {
    ".py", ".md", ".txt", ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".conf",
    ".html", ".htm", ".css", ".scss", ".sass", ".js", ".ts", ".tsx", ".jsx",
    ".sql", ".csv", ".xml", ".env", ".sample", ".prisma", ".sh", ".bash", ".yaml",
}

# ---- Helper Functions for Path and String Manipulation ----
def _norm_rel(path: str) -> str:
    """Normalizes a relative path string."""
    p = (path or "").strip().strip("`").replace("\\", "/")
    return p.lstrip("./").lstrip("/")

def _strip_root_prefix(rel: str, root_name: str) -> str:
    """Removes the project's root folder name from the start of a path."""
    rn = _norm_rel(root_name)
    r = _norm_rel(rel)
    if r.startswith(rn + "/"):
        return r[len(rn) + 1:]
    return r

def _is_texty_name(name: str) -> bool:
    """Checks if a filename suggests it's a text file based on its name or extension."""
    base = name.split("/")[-1]
    if base.startswith(".") and base not in WHITELIST_BARE:
        return False
    if base in WHITELIST_BARE:
        return True
    return os.path.splitext(base)[1].lower() in TEXTY_EXTS

def _safe_normalize(s: str) -> str:
    """Normalizes newlines and removes null bytes from a string."""
    return s.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")