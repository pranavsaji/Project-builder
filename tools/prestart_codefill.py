# tools/prestart_codefill.py
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from tools.codefill import codefill_run, resolve_root_dir

MARKER_NAME = ".codefill.hash"

def _env_true(name: str, default: str = "1") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in {"1", "true", "yes", "on"}

def _resolve_dump(root_dir: Path) -> Optional[Path]:
    p = os.getenv("CODEFILL_DUMP_FILE")
    if p:
        pp = Path(p).expanduser().resolve()
        return pp if pp.exists() else None
    # default: dump.txt inside the Streamlit repo (root of this project)
    cand = Path.cwd() / "dump.txt"
    if cand.exists():
        return cand
    # fallback: project root (destination)
    cand2 = root_dir / "dump.txt"
    return cand2 if cand2.exists() else None

def maybe_run_codefill_once() -> dict:
    if not _env_true("CODEFILL_ENABLE", "1"):
        return {"skipped": True, "reason": "CODEFILL_ENABLE=0"}

    root = resolve_root_dir(os.getenv("CODEFILL_FORCE_BASE_DIR"), os.getenv("CODEFILL_FORCE_ROOT_NAME"))
    dump = _resolve_dump(root)
    if not dump:
        return {"skipped": True, "reason": "dump file not found", "root": str(root)}

    try:
        import hashlib
        new_hash = hashlib.sha256(dump.read_bytes()).hexdigest()
    except Exception:
        new_hash = str(hash(dump.read_text(encoding="utf-8")))

    marker = root / MARKER_NAME
    prev_hash = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""

    if prev_hash and new_hash and prev_hash == str(new_hash):
        return {"skipped": True, "reason": "dump unchanged", "root": str(root)}

    result = codefill_run(
        dump_file=dump,
        root_dir=root,
        mode=os.getenv("CODEFILL_MODE", "overwrite"),
        create_missing=_env_true("CODEFILL_CREATE_MISSING", "1"),
    )

    try:
        marker.write_text(str(new_hash), encoding="utf-8")
    except Exception:
        pass

    return {"skipped": False, "result": result, "root": str(root)}

if __name__ == "__main__":
    out = maybe_run_codefill_once()
    print(json.dumps(out, indent=2, default=str))
