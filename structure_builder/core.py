from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from .sanitize import ensure_under
from .llm_normalizer import normalize_and_maybe_llm, NormalizedDump
from .audit import audit_and_fill


@dataclass
class BuildOptions:
    if_exists: str = "skip"           # "skip" | "overwrite"
    provider: str = "groq"           # "groq" | "openai"
    use_llm_structure: bool = True
    use_llm_backfill: bool = True
    verify_with_llm: bool = True      # NEW: post-build audit & fill
    git_init: bool = False
    use_black: bool = False


@dataclass
class BuildResult:
    root_dir: Path
    created: List[Path]
    skipped: List[Path]
    warnings: List[str]

    def __str__(self) -> str:  # nice for Streamlit
        return f"root={self.root_dir.name}, created={len(self.created)}, skipped={len(self.skipped)}, warnings={len(self.warnings)}"


def _write_file(path: Path, body: str, if_exists: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and if_exists == "skip":
        return False
    path.write_text(body or "", encoding="utf-8")
    return True


def _run(cmd: List[str], cwd: Path, logger: Callable[[str], None] | None):
    try:
        subprocess.run(cmd, cwd=str(cwd), check=True, capture_output=True)
        if logger:
            logger(f"[post] {' '.join(cmd)}")
    except Exception as e:
        if logger:
            logger(f"[post] failed: {' '.join(cmd)} -> {e}")


def build_from_text(
    raw: str,
    dest_folder: str | Path,
    root_hint: Optional[str] = None,
    options: Optional[BuildOptions] = None,
    # --- legacy kwargs supported (Streamlit/CLI) ---
    mode: Optional[str] = None,
    git_init: Optional[bool] = None,
    use_black: Optional[bool] = None,
    use_llm_backfill: Optional[bool] = None,
    use_llm_structure: Optional[bool] = None,
    provider: Optional[str] = None,
    logger: Callable[[str], None] | None = None,
) -> BuildResult:
    """
    Main entry: parse 'raw' description into files and write them under 'dest_folder'.
    Then verify each file by re-matching dump content; if missing, use LLM to retrieve or backfill.
    """
    # Consolidate options
    opts = options or BuildOptions()
    if mode:
        opts.if_exists = mode
    if git_init is not None:
        opts.git_init = git_init
    if use_black is not None:
        opts.use_black = use_black
    if use_llm_backfill is not None:
        opts.use_llm_backfill = use_llm_backfill
    if use_llm_structure is not None:
        opts.use_llm_structure = use_llm_structure
    if provider:
        opts.provider = provider

    dest_base = Path(dest_folder).expanduser().resolve()
    dest_base.mkdir(parents=True, exist_ok=True)

    # Normalize & (optionally) LLM structure/backfill
    dump: NormalizedDump = normalize_and_maybe_llm(
        raw=raw,
        root_hint=root_hint,
        use_llm_structure=opts.use_llm_structure,
        use_llm_backfill=opts.use_llm_backfill,
        provider=opts.provider,
        logger=logger or (lambda _: None),
    )

    root_name = dump.root or "project"
    root_dir = ensure_under(dest_base, root_name)

    created: List[Path] = []
    skipped: List[Path] = []

    # Ensure directories
    for d in dump.tree_dirs:
        (root_dir / d).mkdir(parents=True, exist_ok=True)

    # Write declared files (both from ascii tree & from headings/LLM)
    declared_files = sorted(set(list(dump.tree_files) + list(dump.files_out.keys())))
    for rel in declared_files:
        tgt = ensure_under(root_dir, rel)
        body = dump.files_out.get(rel, "")
        wrote = _write_file(tgt, body, opts.if_exists)
        (created if wrote else skipped).append(tgt)

    # ---- NEW: post-build audit (deterministic match → per-file LLM → backfill) ----
    if opts.verify_with_llm:
        if logger:
            logger("[audit] verifying files against dump...")
        audit_stats = audit_and_fill(
            raw_dump=raw,
            root_dir=root_dir,
            declared_files=declared_files,
            root_name=root_name,
            provider=opts.provider,
            logger=logger,
        )
        if logger:
            logger(f"[audit] created={len(audit_stats['created'])}, "
                   f"updated={len(audit_stats['updated'])}, "
                   f"unchanged={len(audit_stats['unchanged'])}, "
                   f"llm_filled={len(audit_stats['llm_filled'])}, "
                   f"failed={len(audit_stats['failed'])}")

    # Post actions
    if opts.use_black:
        _run(["python", "-m", "black", "."], cwd=root_dir, logger=logger)
    if opts.git_init:
        _run(["git", "init"], cwd=root_dir, logger=logger)
        _run(["git", "add", "-A"], cwd=root_dir, logger=logger)
        _run(["git", "commit", "-m", "Initial scaffold from Structure Builder"], cwd=root_dir, logger=logger)

    return BuildResult(root_dir=root_dir, created=created, skipped=skipped, warnings=dump.warnings)
