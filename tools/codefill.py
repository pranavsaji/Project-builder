# tools/codefill.py
from __future__ import annotations

import os
import re
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Local universal dump parser
from structure_builder.llm_normalizer import parse_dump_bundle

__all__ = ["codefill_run", "resolve_root_dir", "_find_all_files"]

# ---------- Small helpers ----------
def _safe_normalize(s: str) -> str:
    return (s or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")

def _find_all_files(root_dir: Path) -> List[Path]:
    items: List[Path] = []
    for p in root_dir.rglob("*"):
        if p.is_dir():
            # Skip obvious noise dirs
            if p.name in {"__pycache__", ".git", ".mypy_cache", ".venv", "node_modules"}:
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
    return _safe_normalize(existing) != _safe_normalize(new)

def resolve_root_dir(force_base: Optional[str], force_root: Optional[str]) -> Path:
    """
    Resolve a deterministic destination folder. If both hints are present, prefer those;
    otherwise fall back to ~/Downloads/Projects/<project> to match the Streamlit UI defaults.
    """
    if force_base and force_root:
        base = Path(force_base).expanduser()
        root = (base / force_root)
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve()

    default_base = Path(os.getenv("CODEFILL_FORCE_BASE_DIR") or "~/Downloads/Projects").expanduser()
    default_root = os.getenv("CODEFILL_FORCE_ROOT_NAME") or "generated-project"
    try_path = (default_base / default_root)
    try_path.mkdir(parents=True, exist_ok=True)
    return try_path.resolve()

def _read_text_any(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            return _safe_normalize(data.decode(enc))
        except Exception:
            continue
    return _safe_normalize(data.decode("utf-8", errors="replace"))

# ---------- MAIN: Build from dump ----------
def codefill_run(
    dump_file: Path,
    root_dir: Path,
    mode: str = "overwrite",
    create_missing: bool = True,
    logger = print,
) -> Dict[str, object]:
    """
    Universal project builder:
      1) Parse the pasted dump into {root, files[], dirs[]}
      2) Write discovered files (respecting mode)
      3) Optionally create placeholders for tree-only files
    """
    if not dump_file.exists():
        raise FileNotFoundError(f"Dump file not found: {dump_file}")

    raw_dump = _read_text_any(dump_file)
    logger(f"[step] read_dump: ok ({len(raw_dump)} chars)")

    # Pass through to the universal parser/normalizer
    bundle = parse_dump_bundle(raw_dump, root_hint=root_dir.name, logger=logger)

    # If the parser suggests a different root folder, adopt it under the selected base dir
    suggested_root = (bundle.get("root") or "").strip()
    if suggested_root and suggested_root != root_dir.name:
        logger(f"[info] parser suggests root={suggested_root!r}; creating under {root_dir.parent}")
        root_dir = resolve_root_dir(str(root_dir.parent), suggested_root)

    # Materialize
    created, updated = [], []
    found_files: Dict[str, str] = {}

    # 1) Write files that have bodies
    for it in bundle.get("files", []):
        rel = (it.get("path") or "").strip().lstrip("./").lstrip("/")
        body = _safe_normalize(it.get("content") or "")
        if not rel or not body:
            continue
        file_path = root_dir / rel
        file_path.parent.mkdir(parents=True, exist_ok=True)

        existing = file_path.read_text(encoding="utf-8", errors="replace") if file_path.exists() else None
        if _should_write(existing, body, mode):
            file_path.write_text(body, encoding="utf-8")
            (created if existing is None else updated).append(rel)
        found_files[rel] = body

    # 2) Create placeholders for files discovered from trees/headings with no content
    if create_missing:
        for rel in bundle.get("placeholders", []):
            rel = rel.strip().lstrip("./").lstrip("/")
            if not rel or rel in found_files:
                continue
            p = root_dir / rel
            if not p.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                try:
                    p.touch()
                    created.append(rel)
                except Exception:
                    pass

    summary = {
        "root_dir": str(root_dir),
        "created": sorted(set(created)),
        "updated": sorted(set(updated)),
        "from_dump": sorted(found_files.keys()),
        "placeholders": sorted(bundle.get("placeholders", [])),
        "notes": bundle.get("notes", []),
        "count": {
            "created": len(set(created)),
            "updated": len(set(updated)),
            "from_dump": len(found_files),
            "placeholders": len(bundle.get("placeholders", [])),
        },
    }
    logger(f"[step] write_files: ok {json.dumps(summary['count'])}")
    return summary

# NOTE: You would need to add the `llm_batch_backfill` function to `structure_builder/groq_openai.py`.
# It would take the context and list of files, construct the specific prompt shown above,
# and parse the resulting JSON from the LLM.



# # tools/codefill.py
# from __future__ import annotations

# import hashlib
# import os
# import re
# from pathlib import Path
# from typing import Dict, List, Optional, Set, Tuple

# import requests  # noqa: F401

# # LLM wrappers (throttle + retries)
# from tools.llm_utils import llm_call_with_retry

# # Your provider-facing functions (already chunk, etc.)
# from structure_builder.groq_openai import (
#     llm_extract_files,
#     llm_extract_single_file,
#     llm_backfill_file,
# )

# __all__ = [
#     "codefill_run",
#     "resolve_root_dir",
#     "_find_all_files",
# ]

# # ---------------- Patterns ----------------
# HEADING_STRICT_RE = re.compile(
#     r"^\s*#{2,6}\s*(?:`(?P<q>[^`]+)`|(?P<nq>[A-Za-z0-9._/\-]+))\s*$",
#     re.MULTILINE,
# )
# HEADING_ANY_FILE_RE = re.compile(
#     r"^\s*#{1,6}.*`(?P<any>[^`]+)`.*$",
#     re.MULTILINE,
# )
# FENCE_RE = re.compile(
#     r"(?P<fence>(?:```+|~~~+))[^\n]*\n(?P<body>.*?)(?<=\n)(?P=fence)\s*(?:\n|$)",
#     re.DOTALL,
# )
# COMMENT_FILENAME_RE = re.compile(
#     r"""
#     ^\s*
#     (?:
#         (?:\#|//|;|'|--) \s* (?:file\s*:\s*)?
#       | /\* \s* (?:file\s*:\s*)?
#       | <!-- \s* (?:file\s*:\s*)?
#     )
#     (?P<path>[A-Za-z0-9._/\-]+)
#     (?: \s*\*/ | \s*--\> )?
#     \s*$
#     """,
#     re.IGNORECASE | re.VERBOSE,
# )

# # Accept tokens like foo/bar/baz.ext anywhere
# PATH_TOKEN_RE = re.compile(r"(?P<path>[A-Za-z0-9._\-]+(?:/[A-Za-z0-9._\-]+)+)")
# # Optional: backticked files
# TICKED_FILE_RE = re.compile(r"`(?P<path>[A-Za-z0-9._/\-]+)`")

# # Old parser expected ├── / └── — this version also supports ├─ / └─
# TREE_BULLET_RE = re.compile(r"(?:├─+|└─+|─{2,})\s+")
# TREE_PIPE_RE = re.compile(r"[│|]")

# WHITELIST_BARE = {
#     "Dockerfile",
#     "README.md",
#     "README",
#     ".env",
#     ".env.sample",
#     "docker-compose.yml",
#     "requirements.txt",
#     "pyproject.toml",
#     ".gitignore",
# }
# TEXTY_EXTS = {
#     ".py",
#     ".md",
#     ".txt",
#     ".yml",
#     ".yaml",
#     ".json",
#     ".toml",
#     ".ini",
#     ".cfg",
#     ".conf",
#     ".html",
#     ".htm",
#     ".css",
#     ".scss",
#     ".sass",
#     ".js",
#     ".ts",
#     ".tsx",
#     ".jsx",
#     ".sql",
#     ".csv",
#     ".xml",
#     ".env",
#     ".sample",
#     ".prisma",
# }

# # ---------------- Helpers ----------------
# def _norm_rel(path: str) -> str:
#     p = (path or "").strip().strip("`").replace("\\", "/")
#     while p.startswith("./"):
#         p = p[2:]
#     return p.lstrip("/")


# def _strip_root_prefix(rel: str, root_name: str) -> str:
#     rn = _norm_rel(root_name)
#     r = _norm_rel(rel)
#     if r.startswith(rn + "/"):
#         r = r[len(rn) + 1 :]
#     return r


# def _is_texty_name(name: str) -> bool:
#     base = name.split("/")[-1]
#     if base.startswith(".") and base not in WHITELIST_BARE:
#         return False
#     if base in WHITELIST_BARE:
#         return True
#     i = base.rfind(".")
#     return i != -1 and base[i:].lower() in TEXTY_EXTS


# def _dump_hash(text: str) -> str:
#     return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()


# def _strip_fence(text: str) -> str:
#     t = (text or "").strip()
#     if t.startswith("```") or t.startswith("~~~"):
#         lines = t.splitlines()
#         if len(lines) >= 3:
#             return "\n".join(lines[1:-1]).strip()
#     return t


# def _safe_normalize(s: str) -> str:
#     return s.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")


# def _read_text_any(path: Path) -> str:
#     data = path.read_bytes()
#     for enc in ("utf-8", "utf-8-sig", "utf-16", "cp1252", "latin-1"):
#         try:
#             return _safe_normalize(data.decode(enc))
#         except Exception:
#             continue
#     return _safe_normalize(data.decode("utf-8", errors="replace"))


# # ---------------- Discovery ----------------
# def _discover_from_headings(raw: str, root_name: str) -> Set[str]:
#     out: Set[str] = set()
#     for m in HEADING_STRICT_RE.finditer(raw):
#         rel = _strip_root_prefix(
#             _norm_rel(m.group("q") or m.group("nq") or ""), root_name
#         )
#         if rel and ("/" in rel or rel in WHITELIST_BARE or _is_texty_name(rel)):
#             out.add(rel)
#     for m in HEADING_ANY_FILE_RE.finditer(raw):
#         rel = _strip_root_prefix(_norm_rel(m.group("any") or ""), root_name)
#         if rel and ("/" in rel or rel in WHITELIST_BARE or _is_texty_name(rel)):
#             out.add(rel)
#     return out


# def _discover_from_fence_comments(raw: str, root_name: str) -> Set[str]:
#     out: Set[str] = set()
#     for fm in FENCE_RE.finditer(raw):
#         inner = fm.group("body").replace("\r\n", "\n")
#         lines = [ln for ln in inner.split("\n") if ln.strip()][:3]
#         if not lines:
#             continue
#         m = COMMENT_FILENAME_RE.match(lines[0])
#         if m:
#             rel = _strip_root_prefix(_norm_rel(m.group("path")), root_name)
#             if rel and ("/" in rel or rel in WHITELIST_BARE or _is_texty_name(rel)):
#                 out.add(rel)
#     return out


# def _parse_ascii_tree_block(lines: List[str], start_idx: int) -> Tuple[List[str], List[str]]:
#     root_line = lines[start_idx].strip()
#     root = root_line[:-1]  # drop trailing "/"
#     dirs: List[str] = [root]
#     files: List[str] = []
#     stack: List[str] = [root]
#     indent_stack: List[int] = [0]
#     for j in range(start_idx + 1, len(lines)):
#         line = lines[j].rstrip("\n")
#         s = line.strip()
#         if not s or s.startswith("```") or s.startswith("~~~") or s.startswith("# "):
#             break
#         bullets = list(TREE_BULLET_RE.finditer(line))
#         if bullets:
#             name = line[bullets[-1].end() :].split("#", 1)[0].strip()
#             prefix_len = max(0, bullets[-1].start())
#             prefix = line[:prefix_len]
#             depth = max(0, len(TREE_PIPE_RE.findall(prefix)) - 1)
#             while len(stack) > depth + 1:
#                 stack.pop()
#         else:
#             leading = len(line) - len(line.lstrip(" "))
#             name = s.split("#", 1)[0].strip()
#             while indent_stack and leading < indent_stack[-1]:
#                 indent_stack.pop()
#                 if len(stack) > 1:
#                     stack.pop()
#             if leading > indent_stack[-1]:
#                 indent_stack.append(leading)
#         if not name:
#             continue
#         if name.endswith("/"):
#             comp = name[:-1].strip()
#             if comp:
#                 stack.append(comp)
#                 d = "/".join(stack)
#                 dirs.append(d)
#             continue
#         comp = name.split()[0]
#         if comp:
#             rel = "/".join(stack + [comp])
#             files.append(rel)
#     # dedupe keep order
#     return list(dict.fromkeys(dirs)), list(dict.fromkeys(files))


# def _discover_from_tree(raw: str, root_name: str) -> Set[str]:
#     lines = raw.split("\n")
#     root_line = f"{_norm_rel(root_name)}/"
#     out: Set[str] = set()
#     for i, ln in enumerate(lines):
#         if ln.strip() == root_line:
#             _, files = _parse_ascii_tree_block(lines, i)
#             for f in files:
#                 rel = _strip_root_prefix(_norm_rel(f), root_name)
#                 if rel and (_is_texty_name(rel) or rel in WHITELIST_BARE):
#                     out.add(rel)
#             break
#     return out


# def _discover_from_inline_paths(raw: str, root_name: str) -> Set[str]:
#     """
#     Extra safety net:
#       - captures 'Title — packages/foo/bar.ts' (em dash or hyphen)
#       - captures any path-like token 'a/b/c.ext' from the whole dump
#     """
#     out: Set[str] = set()
#     # “title — path.ext” or “title - path.ext”
#     dash_path_re = re.compile(r"[—\-]\s+(?P<path>[A-Za-z0-9._/\-]+)")
#     for m in dash_path_re.finditer(raw):
#         rel = _strip_root_prefix(_norm_rel(m.group("path")), root_name)
#         if rel and ("/" in rel) and (_is_texty_name(rel) or rel in WHITELIST_BARE):
#             out.add(rel)

#     # Any inline path tokens (avoid URLs)
#     for m in PATH_TOKEN_RE.finditer(raw):
#         token = m.group("path")
#         if token.lower().startswith(("http://", "https://")):
#             continue
#         rel = _strip_root_prefix(_norm_rel(token), root_name)
#         if rel and ("/" in rel) and (_is_texty_name(rel) or rel in WHITELIST_BARE):
#             out.add(rel)
#     return out


# def _discover_targets(raw: str, root_name: str) -> Set[str]:
#     targets = set()
#     targets |= _discover_from_headings(raw, root_name)
#     targets |= _discover_from_fence_comments(raw, root_name)
#     targets |= _discover_from_tree(raw, root_name)
#     targets |= _discover_from_inline_paths(raw, root_name)  # NEW: robust inline scan
#     return targets


# # ---------------- Extraction ----------------
# def extract_code_for_path_from_dump(raw_dump: str, root_name: str, rel_path: str) -> Optional[str]:
#     target = _norm_rel(rel_path)
#     candidates = {target, f"{_norm_rel(root_name)}/{target}"}
#     matches: List[Tuple[int, str]] = []
#     for m in HEADING_STRICT_RE.finditer(raw_dump):
#         raw_path = _norm_rel(m.group("q") or m.group("nq") or "")
#         if raw_path in candidates:
#             fence = FENCE_RE.search(raw_dump, m.end())
#             if fence:
#                 matches.append((m.start(), fence.group("body")))
#     for m in HEADING_ANY_FILE_RE.finditer(raw_dump):
#         raw_path = _norm_rel(m.group("any") or "")
#         if raw_path in candidates:
#             fence = FENCE_RE.search(raw_dump, m.end())
#             if fence:
#                 matches.append((m.start(), fence.group("body")))
#     for fm in FENCE_RE.finditer(raw_dump):
#         inner = fm.group("body").replace("\r\n", "\n")
#         first = next((ln for ln in inner.split("\n") if ln.strip()), "")
#         mc = COMMENT_FILENAME_RE.match(first) if first else None
#         if mc and _norm_rel(mc.group("path")) in candidates:
#             matches.append((fm.start(), inner))
#     if not matches:
#         return None
#     matches.sort(key=lambda t: t[0])
#     body = matches[-1][1]
#     return _safe_normalize(_strip_fence(body))


# # ---------------- FS utilities ----------------
# def _find_all_files(root_dir: Path) -> List[Path]:
#     items: List[Path] = []
#     for p in root_dir.rglob("*"):
#         if p.is_dir():
#             if p.name in {"__pycache__", ".git", ".mypy_cache", ".venv", "node_modules"}:
#                 continue
#             continue
#         rel = p.relative_to(root_dir).as_posix()
#         if rel.startswith(".git/"):
#             continue
#         items.append(p)
#     return sorted(items)


# def _should_write(existing: Optional[str], new: str, mode: str) -> bool:
#     if existing is None:
#         return True
#     if mode == "skip":
#         return False
#     return existing != new


# def resolve_root_dir(force_base: Optional[str], force_root: Optional[str]) -> Path:
#     # If user chose both in the UI, honor it.
#     if force_base and force_root:
#         base = Path(force_base).expanduser()
#         root = base / force_root
#         root.mkdir(parents=True, exist_ok=True)
#         return root.resolve()
#     # Sensible fallback (older default)
#     default_base = Path("/Users/pranavsaji/Downloads/Projects").expanduser()
#     default_root = "Logistics-integrations-assistant"
#     try_default = default_base / default_root
#     try_default.mkdir(parents=True, exist_ok=True)
#     return try_default.resolve()


# def _provider() -> str:
#     p = (os.getenv("LLM_PROVIDER") or "groq").strip().lower()
#     return "openai" if p.startswith("openai") else "groq"


# def _have_keys(prov: str) -> bool:
#     return bool(os.getenv("OPENAI_API_KEY")) if prov == "openai" else bool(os.getenv("GROQ_API_KEY"))


# # ---------------- Main ----------------
# def codefill_run(
#     *,
#     dump_file: Path,
#     root_dir: Path,
#     mode: str = "overwrite",
#     create_missing: bool = True,
#     logger=lambda m: None,
# ) -> Dict:
#     """
#     Returns summary + step diagnostics:
#     {
#       created:[], updated:[], unchanged:[],
#       llm_used:[],
#       steps: [{name, status, details}],
#       count:{...}, dump_hash, root_dir, llm_notes:[]
#     }
#     """
#     steps: List[Dict] = []

#     def _step(name: str, status: str, details: str = ""):
#         steps.append({"name": name, "status": status, "details": details})
#         logger(f"[step] {name}: {status} {('- ' + details) if details else ''}")

#     if not dump_file.exists():
#         raise FileNotFoundError(f"Dump file not found: {dump_file}")

#     # Step 1: Read dump
#     raw_dump = _read_text_any(dump_file)
#     _step("read_dump", "ok", f"{len(raw_dump)} chars")

#     provider = _provider()
#     root_name = root_dir.name

#     # Step 2: LLM extract (JSON bundle)
#     llm_files_map: Dict[str, str] = {}
#     llm_notes: List[str] = []

#     use_bundle = os.getenv("LLM_USE_BUNDLE", "0") == "1"  # default OFF
#     fallback_enabled = (os.getenv("LLM_FALLBACK_TO_OPENAI") == "1")
#     os.environ.setdefault("LLM_MAX_TOKENS", os.getenv("LLM_MAX_TOKENS", "2048"))

#     if use_bundle:
#         attempted_providers = [provider]
#         if provider == "groq" and fallback_enabled and _have_keys("openai"):
#             attempted_providers = ["groq", "openai"]

#         for prov in attempted_providers:
#             if not _have_keys(prov):
#                 _step("llm_extract_files", "skip", f"no API key for {prov}")
#                 continue
#             try:
#                 _step("llm_extract_files", "running", f"provider={prov}")
#                 bundle = llm_call_with_retry(
#                     lambda: llm_extract_files(
#                         raw_dump, provider=prov, root_hint=root_name, logger=logger
#                     ),
#                     logger=logger,
#                     tag=f"extract_files[{prov}]",
#                 )
#                 if isinstance(bundle.get("root"), str) and bundle["root"].strip():
#                     root_name = bundle["root"].strip()
#                     logger(f"[codefill] LLM suggests root={root_name!r} ({prov})")
#                 for it in bundle.get("files", []):
#                     rel = _strip_root_prefix(_norm_rel(it.get("path", "")), root_name)
#                     body = it.get("content", "")
#                     if rel and isinstance(body, str):
#                         llm_files_map[rel] = _safe_normalize(body)
#                 llm_notes.extend(bundle.get("notes", []))
#                 _step(
#                     "llm_extract_files",
#                     "ok",
#                     f"provider={prov} files={len(llm_files_map)} notes={len(llm_notes)}",
#                 )
#                 break
#             except Exception as e:
#                 _step("llm_extract_files", "error", f"provider={prov} {e!r}")
#                 if prov == "groq" and fallback_enabled and _have_keys("openai"):
#                     logger("[codefill] bundle failed on groq; trying openai…")
#                     continue
#                 break
#     else:
#         _step("llm_extract_files", "skip", "bundle disabled (using sequential single-file extraction)")

#     # Step 3: Target discovery (regex/tree/inline) + LLM bundle keys
#     targets = _discover_targets(raw_dump, root_name) | set(llm_files_map.keys())

#     # prune: prefer deepest path except whitelisted base files
#     by_base: Dict[str, List[str]] = {}
#     for rel in targets:
#         base = rel.split("/")[-1]
#         by_base.setdefault(base, []).append(rel)
#     pruned: Set[str] = set()
#     for base, rels in by_base.items():
#         if base in WHITELIST_BARE:
#             pruned.update(rels)
#             continue
#         max_depth = max(r.count("/") for r in rels)
#         for r in rels:
#             if r.count("/") == max_depth:
#                 pruned.add(r)
#     targets = pruned

#     # Never treat the *dump file itself* as a target
#     dump_basename = dump_file.name
#     targets = {t for t in targets if t != dump_basename and not t.endswith("/" + dump_basename)}
#     # Skip non-texty + hidden noise like .DS_Store
#     targets = {t for t in targets if _is_texty_name(t) or t in WHITELIST_BARE}

#     _step("discover_targets", "ok", f"{len(targets)} target(s)")

#     # Step 4: Create missing files
#     files: List[Path] = _find_all_files(root_dir)
#     created_placeholders = 0
#     if create_missing:
#         for rel in sorted(targets):
#             p = root_dir / rel
#             if not p.exists():
#                 p.parent.mkdir(parents=True, exist_ok=True)
#                 try:
#                     p.touch()
#                 except Exception:
#                     pass
#                 files.append(p)
#                 created_placeholders += 1
#     _step("create_missing", "ok", f"created_placeholders={created_placeholders}")

#     # Step 5: Main write loop
#     created, updated, unchanged, used_llm = [], [], [], []
#     processed = 0
#     for f in sorted(set(files)):
#         rel = f.relative_to(root_dir).as_posix()

#         # ignore hidden/binary junk
#         if not (_is_texty_name(rel) or rel in WHITELIST_BARE):
#             continue
#         if rel == dump_basename:
#             continue

#         existing = f.read_text(encoding="utf-8", errors="ignore") if f.exists() else None
#         code = ""

#         # Priority 1: LLM JSON body (from bundle)
#         code = llm_files_map.get(rel, "") or ""

#         # Priority 2: literal extraction from dump
#         if not code.strip():
#             code = extract_code_for_path_from_dump(raw_dump, root_name, rel) or ""

#         # Priority 3: sequential single-file LLM rescue
#         provider_order = [_provider()]
#         if provider_order[0] == "groq" and (os.getenv("LLM_FALLBACK_TO_OPENAI") == "1") and _have_keys("openai"):
#             provider_order.append("openai")

#         if not code.strip():
#             for prov in provider_order:
#                 if not _have_keys(prov):
#                     continue
#                 try:
#                     out = llm_call_with_retry(
#                         lambda: llm_extract_single_file(
#                             raw_dump, rel, provider=prov, logger=logger
#                         ),
#                         logger=logger,
#                         tag=f"extract_single:{prov}:{rel}",
#                     )
#                 except Exception as e:
#                     logger(f"[codefill] extract_single failed for {rel} ({prov}): {e}")
#                     out = ""
#                 if isinstance(out, str) and out.strip():
#                     code = out
#                     used_llm.append(rel + f" [single:{prov}]")
#                     break

#         # Priority 4: backfill
#         if not code.strip():
#             for prov in provider_order:
#                 if not _have_keys(prov):
#                     continue
#                 try:
#                     out2 = llm_call_with_retry(
#                         lambda: llm_backfill_file(
#                             rel,
#                             hint="Create a minimal, working file consistent with the project.",
#                             provider=prov,
#                             context=raw_dump,
#                             logger=logger,
#                         ),
#                         logger=logger,
#                         tag=f"backfill:{prov}:{rel}",
#                     )
#                 except Exception as e:
#                     logger(f"[codefill] backfill failed for {rel} ({prov}): {e}")
#                     out2 = ""
#                 if isinstance(out2, str) and out2.strip():
#                     code = out2
#                     used_llm.append(rel + f" [backfill:{prov}]")
#                     break

#         if _should_write(existing, code, mode):
#             f.parent.mkdir(parents=True, exist_ok=True)
#             f.write_text(code, encoding="utf-8")
#             (created if existing is None else updated).append(rel)
#         else:
#             unchanged.append(rel)

#         processed += 1
#         if processed % 10 == 0:
#             _step("write_progress", "ok", f"processed={processed}")

#     summary = {
#         "created": created,
#         "updated": updated,
#         "unchanged": unchanged,
#         "llm_used": used_llm,
#         "count": {
#             "created": len(created),
#             "updated": len(updated),
#             "unchanged": len(unchanged),
#             "llm_used": len(used_llm),
#         },
#         "dump_hash": _dump_hash(raw_dump),
#         "root_dir": str(root_dir),
#         "steps": steps,
#         "llm_notes": llm_notes,
#     }
#     return summary



# # # tools/codefill.py
# # from __future__ import annotations

# # import hashlib
# # import os
# # import re
# # from pathlib import Path
# # from typing import Dict, Iterable, List, Optional, Set, Tuple

# # import requests  # noqa: F401  (used indirectly via groq_openai)
# # from structure_builder.groq_openai import llm_extract_files, llm_extract_single_file, llm_backfill_file


# # # ---------------- Patterns ----------------

# # HEADING_STRICT_RE = re.compile(
# #     r"^\s*#{2,6}\s*(?:`(?P<q>[^`]+)`|(?P<nq>[A-Za-z0-9._/\-]+))\s*$",
# #     re.MULTILINE,
# # )

# # HEADING_ANY_FILE_RE = re.compile(r"^\s*#{1,6}.*`(?P<any>[^`]+)`.*$", re.MULTILINE)

# # FENCE_RE = re.compile(
# #     r"(?P<fence>(?:```+|~~~+))[^\n]*\n(?P<body>.*?)(?<=\n)(?P=fence)\s*(?:\n|$)",
# #     re.DOTALL,
# # )

# # COMMENT_FILENAME_RE = re.compile(
# #     r"""
# #     ^\s*
# #     (?:
# #         (?:\#|//|;|'|--) \s* (?:file\s*:\s*)?
# #       | /\* \s* (?:file\s*:\s*)?
# #       | <!-- \s* (?:file\s*:\s*)?
# #     )
# #     (?P<path>[A-Za-z0-9._/\-]+)
# #     (?: \s*\*/ | \s*--\> )?
# #     \s*$
# #     """,
# #     re.IGNORECASE | re.VERBOSE,
# # )

# # PATH_TOKEN_RE = re.compile(r"(?P<path>[A-Za-z0-9._\-]+(?:/[A-Za-z0-9._\-]+)+)")

# # TICKED_FILE_RE = re.compile(r"`(?P<path>[A-Za-z0-9._/\-]+)`")

# # TREE_BULLET_RE = re.compile(r"(?:├──|└──|──)\s+")
# # TREE_PIPE_RE = re.compile(r"[│|]")


# # WHITELIST_BARE = {
# #     "Dockerfile",
# #     "README.md",
# #     "README",
# #     ".env",
# #     ".env.sample",
# #     "docker-compose.yml",
# #     "requirements.txt",
# #     "pyproject.toml",
# #     ".gitignore",
# # }

# # TEXTY_EXTS = {
# #     ".py", ".md", ".txt", ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".conf",
# #     ".html", ".htm", ".css", ".scss", ".sass", ".js", ".ts", ".tsx", ".jsx",
# #     ".sql", ".csv", ".xml", ".env", ".sample",
# # }


# # # ---------------- Helpers ----------------

# # def _norm_rel(path: str) -> str:
# #     p = (path or "").strip().strip("`").replace("\\", "/")
# #     while p.startswith("./"):
# #         p = p[2:]
# #     return p.lstrip("/")


# # def _strip_root_prefix(rel: str, root_name: str) -> str:
# #     rn = _norm_rel(root_name)
# #     r = _norm_rel(rel)
# #     if r.startswith(rn + "/"):
# #         r = r[len(rn) + 1 :]
# #     return r


# # def _is_texty_name(name: str) -> bool:
# #     base = name.split("/")[-1]
# #     if base in WHITELIST_BARE:
# #         return True
# #     i = base.rfind(".")
# #     return i != -1 and base[i:].lower() in TEXTY_EXTS


# # def _dump_hash(text: str) -> str:
# #     return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()


# # def _strip_fence(text: str) -> str:
# #     t = (text or "").strip()
# #     if t.startswith("```") or t.startswith("~~~"):
# #         lines = t.splitlines()
# #         if len(lines) >= 3:
# #             return "\n".join(lines[1:-1]).strip()
# #     return t


# # def _safe_normalize(s: str) -> str:
# #     return s.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")


# # def _read_text_any(path: Path) -> str:
# #     data = path.read_bytes()
# #     for enc in ("utf-8", "utf-8-sig", "utf-16", "cp1252", "latin-1"):
# #         try:
# #             return _safe_normalize(data.decode(enc))
# #         except Exception:
# #             continue
# #     return _safe_normalize(data.decode("utf-8", errors="replace"))


# # # ---------------- Discovery (regex + robust tree) ----------------

# # def _discover_from_headings(raw: str, root_name: str) -> Set[str]:
# #     out: Set[str] = set()
# #     for m in HEADING_STRICT_RE.finditer(raw):
# #         rel = _strip_root_prefix(_norm_rel(m.group("q") or m.group("nq") or ""), root_name)
# #         if rel and ("/" in rel or rel in WHITELIST_BARE or _is_texty_name(rel)):
# #             out.add(rel)
# #     for m in HEADING_ANY_FILE_RE.finditer(raw):
# #         rel = _strip_root_prefix(_norm_rel(m.group("any") or ""), root_name)
# #         if rel and ("/" in rel or rel in WHITELIST_BARE or _is_texty_name(rel)):
# #             out.add(rel)
# #     return out


# # def _discover_from_fence_comments(raw: str, root_name: str) -> Set[str]:
# #     out: Set[str] = set()
# #     for fm in FENCE_RE.finditer(raw):
# #         inner = fm.group("body").replace("\r\n", "\n")
# #         lines = [ln for ln in inner.split("\n") if ln.strip()][:3]
# #         if not lines:
# #             continue
# #         m = COMMENT_FILENAME_RE.match(lines[0])
# #         if m:
# #             rel = _strip_root_prefix(_norm_rel(m.group("path")), root_name)
# #             if rel and ("/" in rel or rel in WHITELIST_BARE or _is_texty_name(rel)):
# #                 out.add(rel)
# #     return out


# # def _parse_ascii_tree_block(lines: List[str], start_idx: int) -> Tuple[List[str], List[str]]:
# #     """
# #     Parse an ASCII tree starting at lines[start_idx] == '<root>/'.
# #     Returns (dirs, files) with proper nested paths.
# #     """
# #     root_line = lines[start_idx].strip()
# #     root = root_line[:-1]  # drop trailing "/"
# #     dirs: List[str] = [root]
# #     files: List[str] = []
# #     stack: List[str] = [root]
# #     indent_stack: List[int] = [0]

# #     for j in range(start_idx + 1, len(lines)):
# #         line = lines[j].rstrip("\n")
# #         s = line.strip()
# #         if not s or s.startswith("```") or s.startswith("~~~") or s.startswith("# "):
# #             break

# #         # bullet style or plain indent style
# #         bullets = list(TREE_BULLET_RE.finditer(line))
# #         if bullets:
# #             name = line[bullets[-1].end():].split("#", 1)[0].strip()
# #             prefix_len = max(0, bullets[-1].start())
# #             prefix = line[:prefix_len]
# #             depth = max(0, len(TREE_PIPE_RE.findall(prefix)) - 1)
# #             while len(stack) > depth + 1:
# #                 stack.pop()
# #         else:
# #             leading = len(line) - len(line.lstrip(" "))
# #             name = s.split("#", 1)[0].strip()
# #             while indent_stack and leading < indent_stack[-1]:
# #                 indent_stack.pop()
# #                 if len(stack) > 1:
# #                     stack.pop()
# #             if leading > indent_stack[-1]:
# #                 indent_stack.append(leading)

# #         if not name:
# #             continue

# #         if name.endswith("/"):
# #             comp = name[:-1].strip()
# #             if comp:
# #                 stack.append(comp)
# #                 d = "/".join(stack)
# #                 dirs.append(d)
# #             continue

# #         comp = name.split()[0]
# #         if comp:
# #             rel = "/".join(stack + [comp])
# #             files.append(rel)

# #     return list(dict.fromkeys(dirs)), list(dict.fromkeys(files))


# # def _discover_from_tree(raw: str, root_name: str) -> Set[str]:
# #     lines = raw.split("\n")
# #     root_line = f"{_norm_rel(root_name)}/"
# #     out: Set[str] = set()
# #     for i, ln in enumerate(lines):
# #         if ln.strip() == root_line:
# #             _, files = _parse_ascii_tree_block(lines, i)
# #             for f in files:
# #                 rel = _strip_root_prefix(_norm_rel(f), root_name)
# #                 if rel and (_is_texty_name(rel) or rel in WHITELIST_BARE):
# #                     out.add(rel)
# #             break
# #     return out


# # def _discover_targets(raw: str, root_name: str) -> Set[str]:
# #     targets = set()
# #     targets |= _discover_from_headings(raw, root_name)
# #     targets |= _discover_from_fence_comments(raw, root_name)
# #     targets |= _discover_from_tree(raw, root_name)
# #     return targets


# # # ---------------- Extraction ----------------

# # def extract_code_for_path_from_dump(raw_dump: str, root_name: str, rel_path: str) -> Optional[str]:
# #     target = _norm_rel(rel_path)
# #     candidates = {target, f"{_norm_rel(root_name)}/{target}"}
# #     matches: List[Tuple[int, str]] = []

# #     for m in HEADING_STRICT_RE.finditer(raw_dump):
# #         raw_path = _norm_rel(m.group("q") or m.group("nq") or "")
# #         if raw_path in candidates:
# #             fence = FENCE_RE.search(raw_dump, m.end())
# #             if fence:
# #                 matches.append((m.start(), fence.group("body")))

# #     for m in HEADING_ANY_FILE_RE.finditer(raw_dump):
# #         raw_path = _norm_rel(m.group("any") or "")
# #         if raw_path in candidates:
# #             fence = FENCE_RE.search(raw_dump, m.end())
# #             if fence:
# #                 matches.append((m.start(), fence.group("body")))

# #     for fm in FENCE_RE.finditer(raw_dump):
# #         inner = fm.group("body").replace("\r\n", "\n")
# #         first = next((ln for ln in inner.split("\n") if ln.strip()), "")
# #         mc = COMMENT_FILENAME_RE.match(first) if first else None
# #         if mc and _norm_rel(mc.group("path")) in candidates:
# #             matches.append((fm.start(), inner))

# #     if not matches:
# #         return None
# #     matches.sort(key=lambda t: t[0])
# #     body = matches[-1][1]
# #     return _safe_normalize(_strip_fence(body))


# # # ---------------- FS utilities ----------------

# # def _find_all_files(root_dir: Path) -> List[Path]:
# #     items: List[Path] = []
# #     for p in root_dir.rglob("*"):
# #         if p.is_dir():
# #             if p.name in {"__pycache__", ".git", ".mypy_cache", ".venv"}:
# #                 continue
# #             continue
# #         rel = p.relative_to(root_dir).as_posix()
# #         if rel.startswith(".git/"):
# #             continue
# #         items.append(p)
# #     return sorted(items)


# # def _should_write(existing: Optional[str], new: str, mode: str) -> bool:
# #     if existing is None:
# #         return True
# #     if mode == "skip":
# #         return False
# #     return existing != new


# # def resolve_root_dir(force_base: Optional[str], force_root: Optional[str]) -> Path:
# #     if force_base and force_root:
# #         base = Path(force_base).expanduser()
# #         root = (base / force_root)
# #         root.mkdir(parents=True, exist_ok=True)
# #         return root.resolve()
# #     default_base = Path("/Users/pranavsaji/Downloads/Projects").expanduser()
# #     default_root = "Logistics-integrations-assistant"
# #     try_default = (default_base / default_root)
# #     try_default.mkdir(parents=True, exist_ok=True)
# #     return try_default.resolve()


# # # ---------------- LLM helpers ----------------

# # def _provider() -> str:
# #     p = (os.getenv("LLM_PROVIDER") or "groq").strip().lower()
# #     return "openai" if p.startswith("openai") else "groq"


# # def _have_keys(prov: str) -> bool:
# #     return bool(os.getenv("OPENAI_API_KEY")) if prov == "openai" else bool(os.getenv("GROQ_API_KEY"))


# # # ---------------- Main entry ----------------

# # def codefill_run(
# #     *,
# #     dump_file: Path,
# #     root_dir: Path,
# #     mode: str = "overwrite",
# #     create_missing: bool = True,
# #     logger=lambda m: None,
# # ) -> Dict:
# #     if not dump_file.exists():
# #         raise FileNotFoundError(f"Dump file not found: {dump_file}")

# #     raw_dump = _read_text_any(dump_file)
# #     provider = _provider()
# #     root_name = root_dir.name

# #     # Stage A: try to get structure + bodies from LLM (if keys present)
# #     llm_files_map: Dict[str, str] = {}
# #     if _have_keys(provider):
# #         try:
# #             j = llm_extract_files(raw_dump, provider=provider, root_hint=root_name)
# #             if isinstance(j.get("root"), str) and j["root"].strip():
# #                 root_name = j["root"].strip()
# #                 logger(f"[codefill] LLM suggests root={root_name!r}")
# #             for it in j.get("files", []):
# #                 rel = _strip_root_prefix(_norm_rel(it.get("path", "")), root_name)
# #                 body = it.get("content", "")
# #                 if rel and isinstance(body, str):
# #                     llm_files_map[rel] = _safe_normalize(body)
# #             if llm_files_map:
# #                 logger(f"[codefill] LLM provided {len(llm_files_map)} file bodies")
# #         except Exception as e:
# #             logger(f"[codefill] LLM structure pass failed: {e}")

# #     # Stage B: discover targets from dump text
# #     targets = _discover_targets(raw_dump, root_name)
# #     targets |= set(llm_files_map.keys())
# #     logger(f"[codefill] discovered {len(targets)} target(s)")

# #     # Dedupe rule: if the same basename appears at multiple depths, prefer deeper paths,
# #     # unless the basename is in WHITELIST_BARE.
# #     by_base: Dict[str, List[str]] = {}
# #     for rel in targets:
# #         base = rel.split("/")[-1]
# #         by_base.setdefault(base, []).append(rel)
# #     pruned: Set[str] = set()
# #     for base, rels in by_base.items():
# #         if base in WHITELIST_BARE:
# #             pruned.update(rels)
# #             continue
# #         # prefer the longest path(s) (most directories)
# #         max_depth = max(r.count("/") for r in rels)
# #         for r in rels:
# #             if r.count("/") == max_depth:
# #                 pruned.add(r)
# #     targets = pruned

# #     # Ensure placeholders created if requested
# #     files: List[Path] = _find_all_files(root_dir)
# #     if create_missing:
# #         for rel in sorted(targets):
# #             p = (root_dir / rel)
# #             if not p.exists():
# #                 p.parent.mkdir(parents=True, exist_ok=True)
# #                 try:
# #                     p.touch()
# #                 except Exception:
# #                     pass
# #                 files.append(p)

# #     created, updated, unchanged, used_llm = [], [], [], []

# #     # Main write loop
# #     for f in sorted(set(files)):
# #         rel = f.relative_to(root_dir).as_posix()
# #         existing = f.read_text(encoding="utf-8", errors="ignore") if f.exists() else None

# #         # Priority 1: JSON structure body (verbatim)
# #         code = llm_files_map.get(rel, "")

# #         # Priority 2: literal extraction from dump
# #         if not code.strip():
# #             code = extract_code_for_path_from_dump(raw_dump, root_name, rel) or ""

# #         # Priority 3: single-file LLM rescue (exact path)
# #         if not code.strip() and _have_keys(provider):
# #             out = llm_extract_single_file(raw_dump, rel, provider=provider)
# #             if out.strip():
# #                 code = out
# #                 used_llm.append(rel)

# #         # Priority 4: backfill/stub
# #         if not code.strip():
# #             code = llm_backfill_file(rel, hint="Create a minimal, working file consistent with the project.", provider=provider, context=raw_dump)
# #             used_llm.append(rel + " [backfill]")

# #         if _should_write(existing, code, mode):
# #             f.parent.mkdir(parents=True, exist_ok=True)
# #             f.write_text(code, encoding="utf-8")
# #             (created if existing is None else updated).append(rel)
# #         else:
# #             unchanged.append(rel)

# #     return {
# #         "created": created,
# #         "updated": updated,
# #         "unchanged": unchanged,
# #         "llm_used": used_llm,
# #         "count": {
# #             "created": len(created),
# #             "updated": len(updated),
# #             "unchanged": len(unchanged),
# #             "llm_used": len(used_llm),
# #         },
# #         "dump_hash": _dump_hash(raw_dump),
# #         "root_dir": str(root_dir),
# #     }
