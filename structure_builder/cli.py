from __future__ import annotations
import argparse
from pathlib import Path

from .core import build_from_text, BuildOptions

def main(argv=None):
    ap = argparse.ArgumentParser("project-structure-builder")
    ap.add_argument("--dest", required=True, help="Destination directory (parent of project root)")
    ap.add_argument("--root-hint", default=None, help="Root folder name hint")

    # Map CLI flags into BuildOptions
    ap.add_argument("--mode", choices=["skip", "overwrite"], default="skip",
                    help="If a file exists: skip or overwrite")
    ap.add_argument("--provider", choices=["groq", "openai"], default="groq",
                    help="LLM provider (used only if LLM passes are enabled)")
    ap.add_argument("--no-llm-structure", action="store_true", help="Disable LLM structure pass")
    ap.add_argument("--no-llm-backfill", action="store_true", help="Disable LLM backfill pass")

    # Kept for future compatibility; currently not used by core
    ap.add_argument("--black", action="store_true", help="(no-op) reserved for future formatting")
    ap.add_argument("--git", action="store_true", help="(no-op) reserved for future git init & commit")

    ap.add_argument("input_file", help="Path to text dump")

    args = ap.parse_args(argv)
    raw = Path(args.input_file).read_text(encoding="utf-8", errors="ignore")

    opts = BuildOptions(
        if_exists=args.mode,
        provider=args.provider,
        use_llm_structure=(not args.no_llm_structure),
        use_llm_backfill=(not args.no_llm_backfill),
    )

    result = build_from_text(
        raw=raw,
        dest_folder=Path(args.dest),
        root_hint=args.root_hint,
        options=opts,
    )

    # Pretty print summary
    print(f"\nDONE ✅\nRoot: {result.root_dir}")
    print(f"Created ({len(result.created)}):")
    for p in result.created:
        print(f"  + {p.relative_to(result.root_dir)}")
    if result.skipped:
        print(f"Skipped ({len(result.skipped)}):")
        for p in result.skipped:
            print(f"  · {p.relative_to(result.root_dir)}")
    if result.warnings:
        print("\nWarnings:")
        for w in result.warnings:
            print(f"  ! {w}")

if __name__ == "__main__":
    main()
