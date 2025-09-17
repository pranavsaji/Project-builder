from __future__ import annotations
import zipfile
from typing import Dict, List
from pathlib import Path

from .file_harvester import guess_language, should_exclude, TEXT_EXTS

def parse_zipfile(zf: zipfile.ZipFile, max_bytes: int, exclude_tokens: List[str], include_hidden: bool) -> List[Dict]:
    out: List[Dict] = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        rel = info.filename
        if should_exclude(rel, exclude_tokens, include_hidden):
            continue
        # skip very large files
        if info.file_size > max_bytes:
            continue
        # Guess language by extension
        path_like = Path(rel)
        lang = guess_language(path_like)
        # Only attempt text-like files; but we also attempt decode with replacement
        with zf.open(info, "r") as f:
            data = f.read(max_bytes + 1)
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            continue

        out.append({
            "abs_path": f"zip://{rel}",
            "rel_path": rel,
            "language": lang,
            "size": info.file_size,
            "content": text,
        })
    out.sort(key=lambda r: r["rel_path"])
    return out
