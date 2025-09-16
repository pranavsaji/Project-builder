from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple, Dict, List

from .sanitize import ensure_under, sanitize_relpath
from .groq_openai import llm_backfill_file, llm_extract_single_file


# Reuse the same heading/fence patterns as the normalizer.
HEADING_RE = re.compile(
    r"^\s*#{2,6}\s*(?:`(?P<q>[^`]+)`|(?P<nq>[A-Za-z0-9._/\-]+))\s*$",
    re.MULTILINE,
)

FENCE_RE = re.compile(
    r"(?P<fence>(?:```+|~~~+))[^\n]*\n(.*?)\n(?P=fence)\s*",
    re.DOTALL,
)

WHITELIST_BARE_FILENAMES = {
    "Dockerfile", "README.md", "README", ".env", ".env.example",
    "docker-compose.yml", "requirements.txt", "pyproject.toml", ".gitignore",
}


def _extract_from_headings(raw: str, root: str, rel_path: str) -> Optional[str]:
    """
    Deterministically locate a file's code by scanning headings like:
        ## `app/main.py`
        ### app/main.py
    followed immediately by a code fence.
    """
    # accept both "rel" and "root/rel" headings
    candidates = {rel_path, f"{root}/{rel_path}"}

    for m in HEADING_RE.finditer(raw):
        raw_path = m.group("q") or m.group("nq") or ""
        norm = sanitize_relpath(raw_path)
        if not norm:
            continue

        # If heading is bare filename, allow only in whitelist.
        if "/" not in norm and norm not in WHITELIST_BARE_FILENAMES:
            continue

        if norm in candidates:
            fence = FENCE_RE.search(raw, m.end())
            if fence:
                return fence.group(2).replace("\r\n", "\n")
    return None


def _is_empty_or_stub(body: str) -> bool:
    if not body or not body.strip():
        return True
    s = body.strip()
    # Very short / placeholder-y
    if len(s) < 5:
        return True
    # Our own stub marker
    if "Auto-backfilled stub" in s:
        return True
    return False


def _write(path: Path, content: str, *, logger: Callable[[str], None] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if logger:
        logger(f"[audit] wrote: {path}")


def audit_and_fill(
    *,
    raw_dump: str,
    root_dir: Path,
    declared_files: Iterable[str],
    root_name: str,
    provider: str = "groq",
    logger: Callable[[str], None] | None = None,
) -> Dict[str, List[str]]:
    """
    For each declared relative file:
      1) If file is empty/stub → try to extract exactly from dump by path.
      2) If not found → LLM single-file extraction (focused).
      3) If still not found → LLM backfill (contextual stub, but functional).
    Also if file exists and differs from deterministic fence content, we replace with the fence content
    (verbatim wins).
    """
    created: List[str] = []
    updated: List[str] = []
    unchanged: List[str] = []
    llm_filled: List[str] = []
    failed: List[str] = []

    # Normalize & de-dup in a stable order
    rel_list = sorted({p for p in declared_files if sanitize_relpath(p)})

    # Build a quick cache for deterministic lookups to reduce LLM calls when many files point to same fence
    fence_cache: Dict[str, Optional[str]] = {}

    for rel in rel_list:
        try:
            target = ensure_under(root_dir, rel)
        except Exception:
            # Skip unsafe path
            if logger:
                logger(f"[audit] skip unsafe path: {rel}")
            continue

        existing = target.read_text(encoding="utf-8") if target.exists() else ""

        # 1) Deterministic fence lookup by path
        if rel not in fence_cache:
            fence_cache[rel] = _extract_from_headings(raw_dump, root_name, rel)
        fence_content = fence_cache[rel]

        if fence_content and fence_content.strip():
            if not target.exists():
                _write(target, fence_content, logger=logger)
                created.append(rel)
            elif existing != fence_content:
                _write(target, fence_content, logger=logger)
                updated.append(rel)
            else:
                unchanged.append(rel)
            continue

        # 2) If deterministic failed → ask LLM for this file specifically
        if _is_empty_or_stub(existing):
            try:
                llm_body = llm_extract_single_file(raw_dump, rel, provider=provider)
            except Exception as e:
                llm_body = ""
                if logger:
                    logger(f"[audit] llm_extract_single_file error for {rel}: {e}")

            if llm_body and llm_body.strip():
                _write(target, llm_body, logger=logger)
                (created if not existing else updated).append(rel)
                llm_filled.append(rel)
                continue

            # 3) Fallback → LLM backfill
            try:
                backfill = llm_backfill_file(
                    rel,
                    hint="Create a minimal, working file consistent with the project.",
                    provider=provider,
                    context=raw_dump,
                )
            except Exception as e:
                backfill = ""
                if logger:
                    logger(f"[audit] llm_backfill_file error for {rel}: {e}")

            if backfill.strip():
                _write(target, backfill, logger=logger)
                (created if not existing else updated).append(rel)
                llm_filled.append(rel)
            else:
                failed.append(rel)
        else:
            # File exists with non-empty content but no deterministic match; leave as-is
            unchanged.append(rel)

    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "llm_filled": llm_filled,
        "failed": failed,
    }
