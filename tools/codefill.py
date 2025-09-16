# tools/codefill.py
from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

# -------- Robust parsing patterns --------
HEADING_RE = re.compile(
    r"^\s*#{2,6}\s*(?:`(?P<q>[^`]+)`|(?P<nq>[A-Za-z0-9._/\-]+))\s*$",
    re.MULTILINE,
)
# be careful to avoid over-greedy matches across sections
FENCE_RE = re.compile(
    r"(?P<fence>(?:```+|~~~+))[^\n]*\n(?P<body>.*?)(?<=\n)(?P=fence)\s*(?:\n|$)",
    re.DOTALL,
)
WHITELIST_BARE = {
    "Dockerfile",
    "README.md",
    "README",
    ".env",
    ".env.example",
    "docker-compose.yml",
    "requirements.txt",
}

# -------- Helpers --------
def _norm_rel(path: str) -> str:
    p = (path or "").strip().strip("`").replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")

def _dump_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()

def _strip_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```") or t.startswith("~~~"):
        lines = t.splitlines()
        if len(lines) >= 3:
            # drop opening fence (with optional lang) and trailing fence
            return "\n".join(lines[1:-1]).strip()
    return t

def _safe_normalize(s: str) -> str:
    # Normalize newlines + remove nulls that may leak from weird encodings
    return s.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")

def _read_text_any(path: Path) -> str:
    """
    Read a text file with robust decoding fallbacks.
    Order: utf-8 -> utf-8-sig -> utf-16 -> cp1252 -> latin-1 -> utf-8(ignore)
    """
    data = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            return _safe_normalize(data.decode(enc))
        except Exception:
            continue
    # last resort: replace errors
    return _safe_normalize(data.decode("utf-8", errors="replace"))

# -------- Dump extraction --------
def extract_code_for_path_from_dump(raw_dump: str, root_name: str, rel_path: str) -> Optional[str]:
    rel_path = _norm_rel(rel_path)
    candidates = {rel_path, f"{_norm_rel(root_name)}/{rel_path}"}
    matches: List[Tuple[int, str]] = []
    for m in HEADING_RE.finditer(raw_dump):
        raw_path = m.group("q") or m.group("nq") or ""
        norm = _norm_rel(raw_path)
        if "/" not in norm and norm not in WHITELIST_BARE:
            continue
        if norm in candidates:
            fence = FENCE_RE.search(raw_dump, m.end())
            if fence:
                matches.append((m.start(), fence.group("body")))
    if not matches:
        return None
    matches.sort(key=lambda t: t[0])
    body = matches[-1][1]
    return _safe_normalize(_strip_fence(body))

# -------- LLM plumbing --------
def _provider() -> str:
    p = (os.getenv("LLM_PROVIDER") or "groq").strip().lower()
    return "openai" if p.startswith("openai") else "groq"

def _have_keys(prov: str) -> bool:
    return bool(os.getenv("OPENAI_API_KEY")) if prov == "openai" else bool(os.getenv("GROQ_API_KEY"))

def _models() -> Tuple[str, str]:
    return (
        os.getenv("LLM_MODEL_GROQ", "llama-3.1-70b-versatile"),
        os.getenv("LLM_MODEL_OPENAI", "gpt-4o-mini"),
    )

def _chat(provider: str, messages: List[Dict], *, json_mode: bool = False, max_tokens: int = 4096) -> str:
    groq_model, openai_model = _models()
    if provider == "openai":
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY','')}", "Content-Type": "application/json"}
        payload: Dict = {"model": openai_model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        r = requests.post(url, headers=headers, json=payload, timeout=90)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {os.getenv('GROQ_API_KEY','')}", "Content-Type": "application/json"}
    payload = {"model": groq_model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    r = requests.post(url, headers=headers, json=payload, timeout=90)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def _llm_extract(raw_dump: str, rel_path: str, provider: str) -> str:
    if not _have_keys(provider):
        return ""
    sysmsg = (
        "You are a precise file extractor. Given a project dump and a relative path, "
        "return ONLY the code contents for that exact path from the next code-fence under a heading "
        "like ## path or ## `path`. If multiple candidates exist, choose the most complete/latest. "
        "Return code only (no fences or commentary). If not found, return empty."
    )
    usr = f"PATH: {rel_path}\n\nRAW DUMP START\n{raw_dump}\nRAW DUMP END"
    out = _chat(provider, [{"role": "system", "content": sysmsg}, {"role": "user", "content": usr}], max_tokens=7000)
    return _safe_normalize(_strip_fence(out))

def _llm_backfill(path: str, raw_dump: str, provider: str) -> str:
    if not _have_keys(provider):
        return f'"""Auto-backfilled stub for {path}."""\n'
    ext = path.lower().rsplit(".", 1)[-1] if "." in path else ""
    style = {
        "py": "Production-ready Python consistent with FastAPI/SQLAlchemy layout in the dump.",
        "md": "Concise markdown doc.",
        "yml": "Minimal valid YAML.",
        "yaml": "Minimal valid YAML.",
        "dockerfile": "Dockerfile for Python service.",
        "txt": "Plain text.",
    }.get(ext, "Appropriate minimal content.")
    sysmsg = "Fill a missing file for this project. Return ONLY the file contents (no fences)."
    usr = f"Path: {path}\nStyle: {style}\nProject dump:\n{raw_dump}\nReturn only file content."
    out = _chat(_provider(), [{"role": "system", "content": sysmsg}, {"role": "user", "content": usr}], max_tokens=3500)
    out = _strip_fence(out)
    return _safe_normalize(out or f'"""Auto-backfilled stub for {path}."""\n')

# -------- File discovery --------
def _find_all_files(root_dir: Path) -> List[Path]:
    items: List[Path] = []
    for p in root_dir.rglob("*"):
        if p.is_dir():
            if p.name in {"__pycache__", ".git", ".mypy_cache", ".venv"}:
                continue
            continue
        rel = p.relative_to(root_dir).as_posix()
        if rel.startswith(".git/"):
            continue
        items.append(p)
    return sorted(items)

def _should_write(existing: Optional[str], new: str, mode: str) -> bool:
    if existing is None:
        return True
    if mode == "skip":
        return False
    return existing != new

def resolve_root_dir(force_base: Optional[str], force_root: Optional[str]) -> Path:
    if force_base and force_root:
        base = Path(force_base).expanduser()
        root = (base / force_root)
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve()
    # requested hard defaults
    default_base = Path("/Users/pranavsaji/Downloads/Projects").expanduser()
    default_root = "Logistics-integrations-assistant"
    try_default = (default_base / default_root)
    try_default.mkdir(parents=True, exist_ok=True)
    return try_default.resolve()

# -------- Main entry --------
def codefill_run(*, dump_file: Path, root_dir: Path, mode: str = "overwrite", create_missing: bool = True) -> Dict:
    if not dump_file.exists():
        raise FileNotFoundError(f"Dump file not found: {dump_file}")

    raw_dump = _read_text_any(dump_file)
    provider = _provider()
    root_name = root_dir.name

    files = _find_all_files(root_dir)

    if create_missing:
        seen = set()
        for m in HEADING_RE.finditer(raw_dump):
            rawp = m.group("q") or m.group("nq") or ""
            norm = _norm_rel(rawp)
            if "/" not in norm and norm not in WHITELIST_BARE:
                continue
            if norm.startswith(_norm_rel(root_name) + "/"):
                norm = norm[len(_norm_rel(root_name)) + 1 :]
            if norm in seen:
                continue
            seen.add(norm)
            p = (root_dir / norm)
            if not p.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                files.append(p)

    created, updated, unchanged, used_llm = [], [], [], []

    for f in sorted(set(files)):
        rel = f.relative_to(root_dir).as_posix()
        existing = f.read_text(encoding="utf-8", errors="ignore") if f.exists() else None

        # 1) direct extract from dump
        code = extract_code_for_path_from_dump(raw_dump, root_name, rel) or ""
        # 2) llm extract if empty
        if not code.strip():
            out = _llm_extract(raw_dump, rel, provider)
            if out.strip():
                code = out
                used_llm.append(rel)
        # 3) llm backfill as last resort
        if not code.strip():
            code = _llm_backfill(rel, raw_dump, provider)
            used_llm.append(rel + " [backfill]")

        if _should_write(existing, code, mode):
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(code, encoding="utf-8")
            (created if existing is None else updated).append(rel)
        else:
            unchanged.append(rel)

    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "llm_used": used_llm,
        "count": {"created": len(created), "updated": len(updated), "unchanged": len(unchanged), "llm_used": len(used_llm)},
        "dump_hash": _dump_hash(raw_dump),
        "root_dir": str(root_dir),
    }
