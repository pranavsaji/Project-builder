from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .sanitize import clean_component, sanitize_relpath, looks_like_text
from .groq_openai import llm_extract_files, llm_backfill_file


@dataclass
class NormalizedDump:
    root: str
    tree_dirs: List[str] = field(default_factory=list)
    tree_files: List[str] = field(default_factory=list)
    files_out: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


ROOT_LINE_RE = re.compile(r"^\s*([A-Za-z0-9._\-]+)\/\s*$", re.MULTILINE)
BULLET_RE = re.compile(r"(?:├──|└──|──)\s+")
INDENT_BAR_RE = re.compile(r"[│|]")

HEADING_RE = re.compile(
    r"^\s*#{2,6}\s*(?:`(?P<q>[^`]+)`|(?P<nq>[A-Za-z0-9._/\-]+))\s*$",
    re.MULTILINE,
)

# Triple or more backticks/tildes. Match the same fence on close.
FENCE_RE = re.compile(
    r"(?P<fence>(?:```+|~~~+))[^\n]*\n(.*?)\n(?P=fence)\s*",
    re.DOTALL,
)

WHITELIST_BARE_FILENAMES = {
    "Dockerfile",
    "README.md",
    "README",
    ".env",
    ".env.example",
    "docker-compose.yml",
    "requirements.txt",
    "pyproject.toml",
    ".gitignore",
}


def normalize_and_maybe_llm(
    raw: str,
    root_hint: Optional[str],
    logger=lambda m: None,
    use_llm_structure: bool = True,
    use_llm_backfill: bool = True,
    provider: str = "groq",
) -> NormalizedDump:
    """
    Hybrid normalizer: deterministic parse + optional LLM JSON structuring + optional LLM backfill.
    """
    txt = (raw or "").replace("\r\n", "\n")
    root = _find_root_name(txt, root_hint) or "project"

    # Pass 1: deterministic parse
    tree_dirs, tree_files = _parse_ascii_tree_block(txt, root)
    files_from_headings = _extract_files_from_headings(txt, root)
    files_out = {}
    for fp, body in files_from_headings.items():
        if looks_like_text(body):
            files_out[fp] = body

    declared = set(tree_files) | set(files_out.keys())
    warnings = []

    # Pass 2: LLM structure → JSON
    if use_llm_structure:
        try:
            llm_json = llm_extract_files(txt, provider=provider, root_hint=root_hint)
            if llm_json and isinstance(llm_json.get("files"), list):
                r = llm_json.get("root")
                if isinstance(r, str) and r.strip():
                    cand = clean_component(r.strip())
                    if cand:
                        root = cand
                for item in llm_json["files"]:
                    rel = sanitize_relpath(item.get("path", ""))
                    body = item.get("content", "")
                    if not rel or body is None:
                        continue
                    if rel not in files_out or not files_out[rel].strip():
                        if isinstance(body, str):
                            files_out[rel] = body
                    declared.add(rel)
            else:
                warnings.append("LLM structure pass returned no/invalid JSON. Continuing.")
        except Exception as e:
            warnings.append(f"LLM structure pass failed: {e}")

    # Pass 3: per-file backfill (now with full dump context)
    if use_llm_backfill:
        for fp in sorted(declared):
            if fp not in files_out or not (files_out[fp] or "").strip():
                try:
                    files_out[fp] = llm_backfill_file(
                        fp,
                        hint="Create a minimal, working file consistent with the project.",
                        provider=provider,
                        context=txt,
                    )
                except Exception as e:
                    warnings.append(f"[backfill] {fp}: {e}")

    return NormalizedDump(
        root=root,
        tree_dirs=sorted(set(tree_dirs)),
        tree_files=sorted(set(declared)),
        files_out=files_out,
        warnings=warnings,
    )


# ----------------- deterministic helpers -----------------

def _find_root_name(txt: str, root_hint: Optional[str]) -> Optional[str]:
    if root_hint:
        return clean_component(root_hint)
    m = ROOT_LINE_RE.search(txt)
    return clean_component(m.group(1)) if m else None


def _parse_ascii_tree_block(txt: str, root: str) -> Tuple[List[str], List[str]]:
    lines = txt.split("\n")
    root_idx = None
    for i, line in enumerate(lines):
        if line.strip() == root + "/":
            root_idx = i
            break
    if root_idx is None:
        return [], []

    block = []
    for j in range(root_idx, len(lines)):
        line = lines[j]
        if j > root_idx and (
            not line.strip()
            or line.strip().startswith("---")
            or line.strip().startswith("```")
            or line.strip().startswith("~~~")
        ):
            break
        block.append(line.rstrip("\n"))

    dirs = []
    files = []
    stack: List[str] = []

    for idx, raw_line in enumerate(block):
        if idx == 0:
            continue
        line = raw_line.rstrip()
        bullets = list(BULLET_RE.finditer(line))
        name = line[bullets[-1].end():] if bullets else line
        name = name.split("#", 1)[0].strip()
        if not name:
            continue

        prefix_len = len(line) - len(name) - (3 if bullets else 0)
        prefix = line[: max(0, prefix_len)]
        depth = max(0, len(INDENT_BAR_RE.findall(prefix)) - 1)

        if name.endswith("/"):
            comp = clean_component(name[:-1])
            if not comp:
                continue
            while len(stack) > depth:
                stack.pop()
            if len(stack) < depth:
                continue
            stack.append(comp)
            rel_dir = "/".join(stack)
            if rel_dir:
                dirs.append(rel_dir)
            continue

        comp = clean_component(name.split()[0])
        if not comp:
            continue
        while len(stack) > depth:
            stack.pop()
        rel_file = "/".join(stack + [comp]) if stack else comp
        if rel_file:
            files.append(rel_file)

    # include directories implied by files
    for f in files:
        parts = f.split("/")
        if len(parts) > 1:
            d = "/".join(parts[:-1])
            if d:
                dirs.append(d)

    return sorted(set(dirs)), sorted(set(files))


def _extract_files_from_headings(txt: str, root: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in HEADING_RE.finditer(txt):
        raw_path = m.group("q") or m.group("nq") or ""
        rel = sanitize_relpath(raw_path)
        if not rel:
            continue
        fence = FENCE_RE.search(txt, m.end())
        if not fence:
            continue
        body = fence.group(2).replace("\r\n", "\n")

        if "/" not in rel and rel not in WHITELIST_BARE_FILENAMES:
            continue

        if rel.startswith(root + "/"):
            rel = rel[len(root) + 1 :]
        out[rel] = body
    return out
