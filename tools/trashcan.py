from __future__ import annotations
import shutil
import time
from pathlib import Path
from typing import List, Dict

def _ensure_within_base(base_dir: Path, target: Path) -> Path:
    base_dir = base_dir.resolve()
    target = (base_dir / target).resolve()
    # ensure target is inside base_dir
    target.relative_to(base_dir)
    return target

def list_immediate_children(base_dir: Path) -> List[str]:
    """
    Return immediate child names (files & folders) of base_dir as relative paths.
    """
    base_dir = base_dir.resolve()
    if not base_dir.exists() or not base_dir.is_dir():
        return []
    out: List[str] = []
    for p in sorted(base_dir.iterdir(), key=lambda x: x.name.lower()):
        out.append(p.name if p.is_file() else f"{p.name}/")
    return out

def move_to_trash(base_dir: Path, rel_paths: List[str]) -> List[Dict]:
    """
    Move selected files/folders into base_dir/.trash/<timestamp>/...
    Returns list of {"rel_path": ..., "trash_target": ...}
    """
    base_dir = base_dir.resolve()
    ts = time.strftime("%Y%m%d-%H%M%S")
    trash_root = base_dir / ".trash" / ts
    trash_root.mkdir(parents=True, exist_ok=True)

    results: List[Dict] = []
    for rel in rel_paths:
        rel = rel.strip().rstrip("/")  # allow folder UI suffix
        if not rel:
            continue
        target = _ensure_within_base(base_dir, Path(rel))
        if not target.exists():
            # skip missing
            continue
        dest = trash_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(dest))
        results.append({"rel_path": rel, "trash_target": str(dest.relative_to(base_dir))})
    return results

def permanent_delete(base_dir: Path, rel_paths: List[str]) -> List[Dict]:
    """
    Permanently delete selected files/folders under base_dir.
    Returns list of {"rel_path": ..., "type": "file|dir"}.
    """
    base_dir = base_dir.resolve()
    results: List[Dict] = []
    for rel in rel_paths:
        rel = rel.strip().rstrip("/")
        if not rel:
            continue
        target = _ensure_within_base(base_dir, Path(rel))
        if not target.exists():
            continue
        if target.is_dir():
            shutil.rmtree(target)
            results.append({"rel_path": rel, "type": "dir"})
        else:
            target.unlink()
            results.append({"rel_path": rel, "type": "file"})
    return results
