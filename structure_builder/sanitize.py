from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

# allow letters, numbers, -, _, ., and common file chars; trim surrounding spaces
_SAFE_COMP_RE = re.compile(r"^[A-Za-z0-9._\-]+$")

def clean_component(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    s = str(name).strip().replace("\\", "/").strip("/")
    if not s:
        return None
    # take last segment only (prevent paths sneaking in)
    s = s.split("/")[-1]
    return s if _SAFE_COMP_RE.match(s) else None

def sanitize_relpath(p: Optional[str]) -> Optional[str]:
    """Return a safe posix-ish relative path or None."""
    if not p:
        return None
    s = str(p).replace("\\", "/").strip()
    s = re.sub(r"^\./+", "", s)         # strip leading './'
    s = re.sub(r"/+", "/", s)           # collapse slashes
    # remove any .. segments for safety
    parts = [seg for seg in s.split("/") if seg not in ("", ".", "..")]
    if not parts:
        return None
    comps = []
    for seg in parts:
        c = clean_component(seg)
        if not c:
            return None
        comps.append(c)
    return "/".join(comps)

def looks_like_text(content: str) -> bool:
    """Heuristic: true if content is likely text/code."""
    if content is None:
        return False
    # simple binary sniff
    txt = str(content)
    if "\x00" in txt:
        return False
    # too short to be useful
    return len(txt.strip()) >= 1

def ensure_under(base: str | Path, rel: str | Path) -> Path:
    """
    Join base + rel, resolve, and assert the result stays under base.
    Accepts strings, returns Path.
    """
    base_p = Path(base).expanduser().resolve()
    target = (base_p / Path(rel)).resolve()
    # Python 3.11 Path has is_relative_to
    if not target.is_relative_to(base_p):
        raise ValueError(f"Path escapes base: {target} !< {base_p}")
    return target
