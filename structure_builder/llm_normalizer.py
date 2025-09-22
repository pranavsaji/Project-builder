# structure_builder/llm_normalizer.py
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from tools.llm_client import LLMClient

__all__ = [
    "parse_dump_bundle",
    "normalize_and_maybe_llm",
    "NormalizedDump",
    "llm_extract_file_list",
    "llm_extract_file_blobs",
]

@dataclass
class NormalizedDump:
    root: str
    files: List[Dict[str, str]]
    placeholders: List[str]
    notes: List[str]


def _writeln(line: str) -> None:
    if os.getenv("LLM_DEBUG", "0") == "1":
        print(line, flush=True)
    p = os.getenv("LLM_LOG_FILE", "").strip()
    if p:
        from pathlib import Path
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        with Path(p).open("a", encoding="utf-8") as f:
            f.write(line + "\n")


SYSTEM_LIST = """You are a precise project-structure extractor.
You will read a 'dump' that contains code, file trees, and prose.
Return a JSON with the exact list of files that should exist in the project.
Do not invent paths that are not clearly implied by the dump.
If you see multiple roots, pick the dominant root (most files)."""

USER_LIST_TEMPLATE = """Dump begins (may be truncated to fit model limits):
<<<DUMP
{dump}
DUMP>>>

Rules:
- Output strict JSON with this schema:
  {{
    "root": "<best_project_root_or_empty>",
    "files": ["path/one.ext", "dir/path/two.ts", ...]
  }}
- 'root' should be the top-level folder name if obvious; otherwise "".
- Include all source/config files that appear in the dump (do not hallucinate).
- Use forward slashes and do not include directories.
"""

SYSTEM_BLOBS = """You are a precise code extractor.
Given the full dump and a set of file paths, return the exact code for each file path,
verbatim from the dump. If a file doesn't have clear content in the dump, return an empty string."""

USER_BLOBS_TEMPLATE = """Dump begins (may be truncated to fit model limits):
<<<DUMP
{dump}
DUMP>>>

Extract the code bodies for the following files (strict list):
{file_list_json}

Rules:
- Return strict JSON with this schema:
  {{
    "files": [
      {{"path": "path/to/file", "content": "<verbatim content or empty string>"}}
    ]
  }}
- Content must be verbatim from the dump for that path (no stubs).
- If multiple versions exist, pick the most complete/latest.
- If a file is listed but content is unclear, set content to "".
"""


def _client() -> LLMClient:
    return LLMClient()

def _truncate_dump(dump_text: str) -> str:
    max_chars = int(os.getenv("LLM_MAX_CHARS", "90000"))
    if len(dump_text) <= max_chars:
        _writeln(f"[norm] dump_chars={len(dump_text)} (no truncation)")
        return dump_text
    _writeln(f"[norm] dump_chars={len(dump_text)} -> truncating to {max_chars}")
    return dump_text[:max_chars]

def _chunk(lst: List[str], n: int) -> List[List[str]]:
    return [lst[i:i+n] for i in range(0, len(lst), n)]


def llm_extract_file_list(dump_text: str, logger=print) -> Tuple[str, List[str]]:
    dump_text = _truncate_dump(dump_text)
    client = _client()
    messages = [
        {"role": "system", "content": SYSTEM_LIST},
        {"role": "user", "content": USER_LIST_TEMPLATE.format(dump=dump_text)},
    ]
    obj = client.chat_json(messages)
    if not isinstance(obj, dict) or "files" not in obj:
        raise RuntimeError(f"LLM did not return expected list JSON: {obj}")
    root = (obj.get("root") or "").strip()
    files = [str(f).strip().lstrip("./").lstrip("/") for f in obj.get("files", []) if str(f).strip()]
    files = list(dict.fromkeys(files))
    _writeln(f"[norm:list] root={root!r} files={len(files)}")
    logger and logger(f"[llm:list] root={root!r}, files={len(files)}")
    return root, files


def llm_extract_file_blobs(dump_text: str, files: List[str], logger=print) -> List[Dict[str, str]]:
    dump_text = _truncate_dump(dump_text)
    client = _client()
    # Smaller default batch to avoid TPM spikes; can override via env.
    max_batch = int(os.getenv("LLM_MAX_BATCH", "6"))
    all_results: Dict[str, str] = {}

    batches = _chunk(files, max_batch)
    _writeln(f"[norm:blobs] total_files={len(files)} batches={len(batches)} batch_size={max_batch}")

    for idx, batch in enumerate(batches, start=1):
        payload = {"files": [{"path": p} for p in batch]}
        messages = [
            {"role": "system", "content": SYSTEM_BLOBS},
            {"role": "user", "content": USER_BLOBS_TEMPLATE.format(
                dump=dump_text,
                file_list_json=json.dumps(payload, indent=2)
            )},
        ]
        _writeln(f"[norm:blobs] sending batch {idx}/{len(batches)} size={len(batch)}")
        obj = client.chat_json(messages)
        if not isinstance(obj, dict) or "files" not in obj:
            raise RuntimeError(f"LLM did not return expected blobs JSON: {obj}")

        received = 0
        for item in obj["files"]:
            path = (item.get("path") or "").strip().lstrip("./").lstrip("/")
            content = item.get("content") or ""
            if path:
                all_results[path] = content
                received += 1

        _writeln(f"[norm:blobs] batch {idx} received={received}")
        logger and logger(f"[llm:blobs] batch={idx}/{len(batches)} -> received={received}")

    return [{"path": p, "content": all_results.get(p, "")} for p in files]


# -----------------------------
# Public API
# -----------------------------

def parse_dump_bundle(raw_dump: str, root_hint: Optional[str] = None, logger=print) -> Dict[str, object]:
    root, files = llm_extract_file_list(raw_dump, logger=logger)
    if not root and root_hint:
        root = root_hint

    blobs = llm_extract_file_blobs(raw_dump, files, logger=logger)

    return {
        "root": root or (root_hint or "generated-project"),
        "files": blobs,
        "placeholders": [],
        "notes": [],
    }


def normalize_and_maybe_llm(
    raw_dump: str,
    root_hint: Optional[str] = None,
    use_llm: bool = True,
    logger=print,
) -> NormalizedDump:
    bundle = parse_dump_bundle(raw_dump, root_hint=root_hint, logger=logger)
    return NormalizedDump(
        root=(bundle.get("root") or (root_hint or "generated-project")),
        files=bundle.get("files", []),
        placeholders=bundle.get("placeholders", []),
        notes=bundle.get("notes", []),
    )

    
# from __future__ import annotations

# import re
# from dataclasses import dataclass, field
# from typing import Dict, List, Optional, Tuple

# from .sanitize import clean_component, sanitize_relpath, looks_like_text
# from .groq_openai import llm_extract_files, llm_backfill_file


# @dataclass
# class NormalizedDump:
#     root: str
#     tree_dirs: List[str] = field(default_factory=list)
#     tree_files: List[str] = field(default_factory=list)
#     files_out: Dict[str, str] = field(default_factory=dict)
#     warnings: List[str] = field(default_factory=list)


# # ---------- patterns ----------

# ROOT_LINE_RE = re.compile(r"^\s*([A-Za-z0-9._\-]+)\/\s*$", re.MULTILINE)

# BULLET_RE = re.compile(r"(?:├──|└──|──)\s+")
# INDENT_BAR_RE = re.compile(r"[│|]")

# # Strict: heading is exactly the filename
# HEADING_STRICT_RE = re.compile(
#     r"^\s*#{2,6}\s*(?:`(?P<q>[^`]+)`|(?P<nq>[A-Za-z0-9._/\-]+))\s*$",
#     re.MULTILINE,
# )

# # NEW: any heading line that contains a backticked path/filename anywhere
# HEADING_ANY_FILE_RE = re.compile(
#     r"^\s*#{1,6}.*`(?P<any>[^`]+)`.*$",
#     re.MULTILINE,
# )

# # Any fenced block (``` or ~~~), capture inner as group(2)
# FENCE_RE = re.compile(r"(?P<fence>(?:```+|~~~+))[^\n]*\n(.*?)\n(?P=fence)\s*", re.DOTALL)

# # First line in fence is a comment that names a file
# COMMENT_FILENAME_RE = re.compile(
#     r"""
#     ^\s*
#     (?:
#         (?:\#|//|;|'|--) \s* (?:file\s*:)?     # py/sh/sql comments
#       | /\* \s* (?:file\s*:)?                  # /* file: path */
#       | <!-- \s* (?:file\s*:)?                 # <!-- file: path -->
#     )
#     (?P<path>[A-Za-z0-9._/\-]+)
#     (?: \s*\*/ | \s*--\> )?
#     \s*$
#     """,
#     re.IGNORECASE | re.VERBOSE,
# )

# # Path-like token (with at least one slash)
# PATH_TOKEN_RE = re.compile(r"(?P<path>[A-Za-z0-9._\-]+(?:/[A-Za-z0-9._\-]+)+)")
# # NEW: backticked filename/path (allows bare names, e.g. `bulk_mailer.py`)
# TICKED_FILE_RE = re.compile(r"`(?P<path>[A-Za-z0-9._/\-]+)`")

# WHITELIST_BARE_FILENAMES = {
#     "Dockerfile", "README.md", "README", ".env", ".env.example",
#     "docker-compose.yml", "requirements.txt", "pyproject.toml", ".gitignore",
# }
# SKIP_FILENAMES = {".DS_Store"}

# TEXTY_EXTS = {
#     ".py",".md",".txt",".yml",".yaml",".json",".toml",".ini",".cfg",".conf",
#     ".html",".htm",".css",".scss",".sass",".js",".ts",".tsx",".jsx",
#     ".sql",".csv",".xml",".env",
# }


# def normalize_and_maybe_llm(
#     raw: str,
#     root_hint: Optional[str],
#     logger=lambda m: None,
#     use_llm_structure: bool = True,
#     use_llm_backfill: bool = True,
#     provider: str = "groq",
# ) -> NormalizedDump:
#     """
#     Deterministic parse + resilient heuristics + optional LLM fill.
#     """
#     txt = (raw or "").replace("\r\n", "\n")

#     # Root guess
#     root = _find_root_name(txt, root_hint) or _guess_root_from_paths(txt) or "project"
#     logger(f"[normalizer] root={root!r}")

#     # PASS 1: parse tree (ascii or indent under 'root/')
#     tree_dirs, tree_files = _parse_ascii_or_indent_tree(txt, root)
#     logger(f"[normalizer] tree dirs={len(tree_dirs)} files={len(tree_files)}")

#     files_out: Dict[str, str] = {}

#     # a) headings that are exactly the filename
#     h_strict = _extract_files_from_headings_strict(txt, root)
#     logger(f"[normalizer] strict heading matches: {len(h_strict)}")
#     _merge_found(files_out, h_strict, logger)

#     # b) headings that contain a backticked filename anywhere
#     h_any = _extract_files_from_headings_any(txt, root)
#     logger(f"[normalizer] loose heading matches: {len(h_any)}")
#     _merge_found(files_out, h_any, logger)

#     # c) fences with first-line filename comments
#     cmt = _extract_by_firstline_filename_comment(txt, root)
#     logger(f"[normalizer] comment-in-fence matches: {len(cmt)}")
#     _merge_found(files_out, cmt, logger)

#     # d) nearby tokens (before fence / first lines inside)
#     near = _extract_by_nearby_path_tokens(txt, root)
#     logger(f"[normalizer] nearby-token matches: {len(near)}")
#     _merge_found(files_out, near, logger)

#     declared = set(tree_files) | set(files_out.keys())
#     declared = {f for f in declared if f and f.split("/")[-1] not in SKIP_FILENAMES}
#     warnings: List[str] = []

#     # PASS 2: LLM structure (optional)
#     if use_llm_structure:
#         try:
#             llm_json = llm_extract_files(txt, provider=provider, root_hint=root_hint)
#             if llm_json and isinstance(llm_json.get("files"), list):
#                 r = llm_json.get("root")
#                 if isinstance(r, str) and r.strip():
#                     cand = clean_component(r.strip())
#                     if cand:
#                         root = cand
#                         logger(f"[normalizer] LLM suggested root={root!r}")
#                 added = 0
#                 for item in llm_json["files"]:
#                     rel = sanitize_relpath(item.get("path", ""))
#                     body = item.get("content", "")
#                     if not rel or body is None:
#                         continue
#                     if rel not in files_out or not files_out[rel].strip():
#                         if isinstance(body, str) and looks_like_text(body):
#                             files_out[rel] = body
#                             added += 1
#                     declared.add(rel)
#                 logger(f"[normalizer] LLM structure added {added} file(s)")
#             else:
#                 warnings.append("LLM structure pass returned no/invalid JSON. Continuing.")
#         except Exception as e:
#             warnings.append(f"LLM structure pass failed: {e}")

#     # PASS 3: backfill missing (optional)
#     if use_llm_backfill:
#         miss = [fp for fp in sorted(declared) if fp not in files_out or not (files_out[fp] or "").strip()]
#         logger(f"[normalizer] backfill missing={len(miss)}")
#         for fp in miss:
#             try:
#                 files_out[fp] = llm_backfill_file(
#                     fp,
#                     hint="Create a minimal, working file consistent with the project.",
#                     provider=provider,
#                     context=txt,
#                 )
#             except Exception as e:
#                 warnings.append(f"[backfill] {fp}: {e}")

#     return NormalizedDump(
#         root=root,
#         tree_dirs=sorted(set(tree_dirs)),
#         tree_files=sorted(set(declared)),
#         files_out=files_out,
#         warnings=warnings,
#     )


# # ----------------- helpers -----------------

# def _merge_found(dst: Dict[str, str], src: Dict[str, str], logger) -> None:
#     if not src:
#         return
#     for k, v in src.items():
#         if k not in dst or not dst[k].strip():
#             dst[k] = v

# def _find_root_name(txt: str, root_hint: Optional[str]) -> Optional[str]:
#     if root_hint:
#         return clean_component(root_hint)
#     m = ROOT_LINE_RE.search(txt)
#     return clean_component(m.group(1)) if m else None

# def _guess_root_from_paths(txt: str) -> Optional[str]:
#     counts: Dict[str, int] = {}
#     for pm in PATH_TOKEN_RE.finditer(txt):
#         path = pm.group("path")
#         parts = path.split("/")
#         if not parts:
#             continue
#         top = clean_component(parts[0])
#         if not top:
#             continue
#         counts[top] = counts.get(top, 0) + 1
#     if not counts:
#         return None
#     top = max(counts.items(), key=lambda kv: kv[1])[0]
#     return top or None


# def _parse_ascii_or_indent_tree(txt: str, root: str) -> Tuple[List[str], List[str]]:
#     lines = txt.split("\n")
#     root_idx = None
#     for i, line in enumerate(lines):
#         if line.strip() == root + "/":
#             root_idx = i
#             break
#     if root_idx is None:
#         return [], []

#     block = []
#     for j in range(root_idx, len(lines)):
#         line = lines[j]
#         if j > root_idx and (
#             not line.strip()
#             or line.strip().startswith("---")
#             or line.strip().startswith("```")
#             or line.strip().startswith("~~~")
#             or line.strip().startswith("# ")
#         ):
#             break
#         block.append(line.rstrip("\n"))

#     dirs: List[str] = []
#     files: List[str] = []
#     stack: List[str] = []
#     indent_stack: List[int] = [0]

#     for idx, raw_line in enumerate(block):
#         if not raw_line.strip():
#             continue

#         if idx == 0:
#             if raw_line.strip().endswith("/"):
#                 comp = clean_component(raw_line.strip()[:-1])
#                 if comp:
#                     stack = [comp]
#                     dirs.append(comp)
#                     indent_stack = [0]
#             continue

#         line = raw_line.rstrip("\t ")
#         bullets = list(BULLET_RE.finditer(line))
#         if bullets:
#             name = line[bullets[-1].end():].split("#", 1)[0].strip()
#             prefix_len = len(line) - len(name) - 3
#             prefix = line[: max(0, prefix_len)]
#             depth = max(0, len(INDENT_BAR_RE.findall(prefix)) - 1)
#             while len(stack) > depth + 1:
#                 stack.pop()
#         else:
#             leading = len(line) - len(line.lstrip(" "))
#             name = line.strip().split("#", 1)[0].strip()
#             while indent_stack and leading < indent_stack[-1]:
#                 indent_stack.pop()
#                 if len(stack) > 1:
#                     stack.pop()
#             if leading > indent_stack[-1]:
#                 indent_stack.append(leading)
#             depth = len(stack) - 1

#         if not name:
#             continue

#         if name.endswith("/"):
#             comp = clean_component(name[:-1])
#             if not comp:
#                 continue
#             while len(stack) > depth + 1:
#                 stack.pop()
#             if len(stack) < depth + 1:
#                 continue
#             stack.append(comp)
#             rel_dir = "/".join(stack)
#             if rel_dir:
#                 dirs.append(rel_dir)
#             continue

#         comp = clean_component(name.split()[0])
#         if not comp:
#             continue
#         while len(stack) > (depth + 1):
#             stack.pop()
#         rel_file = "/".join(stack + [comp]) if stack else comp
#         if rel_file.split("/")[-1] in SKIP_FILENAMES:
#             continue
#         files.append(rel_file)

#     for f in files:
#         parts = f.split("/")
#         if len(parts) > 1:
#             d = "/".join(parts[:-1])
#             if d:
#                 dirs.append(d)

#     return sorted(set(dirs)), sorted(set(files))


# def _extract_files_from_headings_strict(txt: str, root: str) -> Dict[str, str]:
#     out: Dict[str, str] = {}
#     for m in HEADING_STRICT_RE.finditer(txt):
#         raw_path = m.group("q") or m.group("nq") or ""
#         rel = _normalize_rel_from_raw(raw_path, root)
#         if not rel:
#             continue
#         fence = FENCE_RE.search(txt, m.end())
#         if not fence:
#             continue
#         body = fence.group(2).replace("\r\n", "\n")
#         if looks_like_text(body):
#             out[rel] = body
#     return out

# def _extract_files_from_headings_any(txt: str, root: str) -> Dict[str, str]:
#     """
#     Match headings that contain a backticked filename anywhere on the line,
#     e.g. '# 2) Code — `bulk_mailer.py`'
#     """
#     out: Dict[str, str] = {}
#     for m in HEADING_ANY_FILE_RE.finditer(txt):
#         raw_path = m.group("any") or ""
#         rel = _normalize_rel_from_raw(raw_path, root)
#         if not rel:
#             continue
#         fence = FENCE_RE.search(txt, m.end())
#         if not fence:
#             continue
#         body = fence.group(2).replace("\r\n", "\n")
#         if looks_like_text(body):
#             out[rel] = body
#     return out


# def _extract_by_firstline_filename_comment(txt: str, root: str) -> Dict[str, str]:
#     out: Dict[str, str] = {}
#     for fm in FENCE_RE.finditer(txt):
#         inner = fm.group(2).replace("\r\n", "\n")
#         lines = [ln for ln in inner.split("\n") if ln.strip()][:3]
#         rel: Optional[str] = None
#         drop_first = False
#         if lines:
#             m = COMMENT_FILENAME_RE.match(lines[0])
#             if m:
#                 rel = _normalize_rel_from_raw(m.group("path"), root)
#                 drop_first = True
#         if not rel:
#             continue
#         body = inner
#         if drop_first and inner.lstrip().startswith(lines[0]):
#             body = "\n".join(inner.split("\n", 1)[1:]).lstrip("\n")
#         if looks_like_text(body):
#             out[rel] = body
#     return out


# def _extract_by_nearby_path_tokens(txt: str, root: str) -> Dict[str, str]:
#     """
#     For each fenced block:
#       • look inside the first few lines for a comment or `backticked` path,
#       • else scan up to 8 lines above the fence for path/backticked tokens,
#       • accept bare filenames with texty extensions.
#     """
#     out: Dict[str, str] = {}
#     lines = txt.split("\n")

#     offsets = [0]
#     for ln in lines:
#         offsets.append(offsets[-1] + len(ln) + 1)

#     def _line_no(pos: int) -> int:
#         lo = 0
#         while lo + 1 < len(offsets) and offsets[lo + 1] <= pos:
#             lo += 1
#         return lo

#     for fm in FENCE_RE.finditer(txt):
#         inner = fm.group(2).replace("\r\n", "\n")

#         candidate: Optional[str] = None

#         # Inside fence: first lines
#         inner_lines = [ln for ln in inner.split("\n") if ln.strip()][:5]
#         for ln in inner_lines:
#             m = COMMENT_FILENAME_RE.match(ln)
#             if m:
#                 candidate = m.group("path")
#                 break
#             mt = TICKED_FILE_RE.search(ln)
#             if mt:
#                 candidate = mt.group("path")
#                 break
#             mp = PATH_TOKEN_RE.search(ln)
#             if mp:
#                 candidate = mp.group("path")
#                 break

#         # Above fence
#         if not candidate:
#             start_line = max(0, _line_no(fm.start()) - 8)
#             context = "\n".join(lines[start_line:_line_no(fm.start())])
#             # prefer last token (closest)
#             mt = list(TICKED_FILE_RE.finditer(context))
#             if mt:
#                 candidate = mt[-1].group("path")
#             else:
#                 mp = list(PATH_TOKEN_RE.finditer(context))
#                 if mp:
#                     candidate = mp[-1].group("path")

#         if not candidate:
#             continue

#         rel = _normalize_rel_from_raw(candidate, root)
#         if not rel or not _is_texty(rel):
#             continue

#         body = inner
#         if looks_like_text(body) and rel not in out:
#             out[rel] = body

#     return out


# # ---------- small utils ----------

# def _normalize_rel_from_raw(raw_path: str, root: str) -> Optional[str]:
#     rel = sanitize_relpath(raw_path or "")
#     if not rel:
#         return None
#     # NEW: allow bare filenames if they have a texty extension or are whitelisted
#     if "/" not in rel and rel not in WHITELIST_BARE_FILENAMES:
#         name = rel.split("/")[-1]
#         i = name.rfind(".")
#         if i == -1:
#             return None
#         ext = name[i:].lower()
#         if ext not in TEXTY_EXTS:
#             return None
#     if rel.startswith(root + "/"):
#         rel = rel[len(root) + 1 :]
#     if rel.split("/")[-1] in SKIP_FILENAMES:
#         return None
#     return rel

# def _is_texty(rel: str) -> bool:
#     name = rel.split("/")[-1]
#     if name in WHITELIST_BARE_FILENAMES:
#         return True
#     i = name.rfind(".")
#     return i != -1 and name[i:].lower() in TEXTY_EXTS
