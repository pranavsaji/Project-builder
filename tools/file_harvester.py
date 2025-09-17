from __future__ import annotations
import os
from pathlib import Path
from typing import Dict, List

TEXT_EXTS = {
    ".py", ".txt", ".md", ".yaml", ".yml", ".json", ".toml", ".ini",
    ".xml", ".csv", ".tsv", ".html", ".htm", ".css", ".scss", ".sass",
    ".js", ".ts", ".tsx", ".jsx", ".env", ".dockerignore", ".gitignore",
    ".sh", ".bash", ".zsh", ".sql", ".cfg", ".prisma", ".proto",
    ".dockerfile", "dockerfile",
}

LANG_FROM_EXT = {
    ".py": "python",
    ".md": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".ini": "ini",
    ".xml": "xml",
    ".csv": "csv",
    ".tsv": "text",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".sass": "sass",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "jsx",
    ".env": "bash",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".sql": "sql",
    ".proto": "protobuf",
    ".dockerfile": "docker",
}

def guess_language(path: Path) -> str:
    name = path.name.lower()
    if name == "dockerfile":
        return "docker"
    ext = path.suffix.lower()
    return LANG_FROM_EXT.get(ext, "text")

def looks_textual(path: Path) -> bool:
    name = path.name.lower()
    if name == "dockerfile":
        return True
    return path.suffix.lower() in TEXT_EXTS

def should_exclude(rel_path: str, exclude_tokens: List[str], include_hidden: bool) -> bool:
    p = rel_path
    if not include_hidden:
        # skip hidden files or any segment starting with .
        if any(seg.startswith(".") for seg in Path(p).parts):
            return True
    # token contains match
    p_lower = p.lower()
    for tok in exclude_tokens:
        if tok.lower() in p_lower:
            return True
    return False

def read_text_safe(path: Path, max_bytes: int) -> str | None:
    try:
        size = path.stat().st_size
        if size > max_bytes:
            return None
        with path.open("rb") as f:
            data = f.read(max_bytes + 1)
        # decode safely
        return data.decode("utf-8", errors="replace")
    except Exception:
        return None

def harvest_folder(base_dir: Path, max_bytes: int, exclude_tokens: List[str], include_hidden: bool) -> List[Dict]:
    out: List[Dict] = []
    for fs_path in base_dir.rglob("*"):
        if fs_path.is_dir():
            continue
        rel = os.path.relpath(fs_path, base_dir)
        if should_exclude(rel, exclude_tokens, include_hidden):
            continue
        if not looks_textual(fs_path):
            # try to read a bit anyway — treat as text if decodes
            content = read_text_safe(fs_path, max_bytes)
            if content is None:
                continue
            lang = "text"
        else:
            content = read_text_safe(fs_path, max_bytes)
            if content is None:
                continue
            lang = guess_language(fs_path)

        out.append({
            "abs_path": str(fs_path),
            "rel_path": rel.replace("\\", "/"),
            "language": lang,
            "size": fs_path.stat().st_size,
            "content": content,
        })
    # stable sort by rel_path
    out.sort(key=lambda r: r["rel_path"])
    return out
