# tools/ui_utils.py
from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Tuple

__all__ = ["launch_editor", "open_in_vscode", "button_key"]


# ---------------------------
# Streamlit key helper
# ---------------------------

def button_key(*parts: object) -> str:
    """
    Build a deterministic, collision-resistant Streamlit key from any number of parts.
    Examples:
      button_key("rm", "apps/api/src/env.ts")
      button_key("open", Path("/tmp/foo.txt"))
    """
    # Normalize to text
    strs = [str(p) for p in parts if p is not None]
    if not strs:
        base = "key"
    else:
        base = strs[0].lower().replace(" ", "_")
    # Hash all parts for uniqueness (keeps key short + deterministic)
    h = hashlib.sha1(("||".join(strs)).encode("utf-8")).hexdigest()[:10]
    return f"btn::{base}::{h}"


# ---------------------------
# Editor launchers
# ---------------------------

def _run(cmd: Iterable[str]) -> Tuple[bool, str]:
    try:
        res = subprocess.run(list(cmd), check=False, capture_output=True, text=True)
        if res.returncode == 0:
            return True, ""
        return False, f"exit {res.returncode}; stdout={res.stdout.strip()} stderr={res.stderr.strip()}"
    except FileNotFoundError as e:
        return False, f"not found: {e}"
    except Exception as e:
        return False, str(e)


def _exists(p: Path) -> bool:
    try:
        return p.exists()
    except Exception:
        return False


def _normalize_target(target: str | os.PathLike) -> Path:
    p = Path(target).expanduser().resolve()
    if _exists(p):
        # If they pass a file, open its parent in VS Code (better UX)
        return p if p.is_dir() else p.parent
    return p


def open_in_vscode(target: str | os.PathLike) -> Tuple[bool, str]:
    """
    Try very hard to open `target` in VS Code.
    Order of attempts:
      1) EDITOR_CMD env, if set (must be a command that accepts a path)
      2) `code <path>` if the shell command is installed
      3) macOS: `open -a "Visual Studio Code" <path>`
      4) Windows: PowerShell Start-Process (if code found) or Explorer fallback
      5) Linux: xdg-open folder fallback
    Returns (ok, message).
    """
    target_path = _normalize_target(target)
    editor_cmd = os.getenv("EDITOR_CMD", "").strip()
    visual = os.getenv("VISUAL", "").strip()

    # 1) Respect EDITOR_CMD / VISUAL
    last_err = ""
    for env_cmd in (editor_cmd, visual):
        if env_cmd:
            parts = shlex.split(env_cmd) + [str(target_path)]
            ok, msg = _run(parts)
            if ok:
                return True, f"Opened with EDITOR_CMD/VISUAL: {' '.join(parts[:-1])}"
            last_err = msg

    # 2) Prefer `code` when available
    code_path = shutil.which("code")
    if code_path:
        ok, msg = _run([code_path, str(target_path)])
        if ok:
            return True, "Opened with VS Code (`code`)."
        last_err = msg
    else:
        last_err = "`code` not found on PATH"

    # 3) macOS fallback
    if sys.platform == "darwin":
        ok, msg = _run(["open", "-a", "Visual Studio Code", str(target_path)])
        if ok:
            return True, 'Opened with macOS `open -a "Visual Studio Code"`.'
        return False, (
            "Could not launch VS Code. Last error: "
            f"{msg or last_err}\n"
            "Tip: In VS Code → Cmd+Shift+P → “Shell Command: Install ‘code’ command in PATH”."
        )

    # 4) Windows fallback
    if os.name == "nt":
        if code_path:
            ok, msg = _run([
                "powershell", "-NoProfile", "-Command",
                f"Start-Process -FilePath {shlex.quote(code_path)} -ArgumentList {shlex.quote(str(target_path))}"
            ])
            if ok:
                return True, "Opened with VS Code (PowerShell)."
            return False, f"Could not launch VS Code. Last error: {msg or last_err}"
        ok, msg = _run(["explorer", str(target_path)])
        if ok:
            return True, "VS Code CLI not found; opened folder in Explorer."
        return False, f"Could not launch VS Code. Last error: {msg or last_err}"

    # 5) Linux fallback: open folder in default file manager
    ok, msg = _run(["xdg-open", str(target_path)])
    if ok:
        return True, "VS Code CLI not found; opened folder via xdg-open."
    return False, f"Could not launch VS Code. Last error: {msg or last_err}"


def launch_editor(target: str | os.PathLike, prefer: str = "vscode") -> Tuple[bool, str]:
    """Public entrypoint used by Streamlit pages."""
    if prefer in ("vscode", "code", "vs_code", "visualstudiocode"):
        return open_in_vscode(target)
    return open_in_vscode(target)



# # tools/ui_utils.py
# from __future__ import annotations

# import hashlib
# import os
# import shlex
# import shutil
# import subprocess
# import sys
# from pathlib import Path
# from typing import List

# import streamlit as st

# def launch_editor(target: Path) -> str:
#     """
#     Launches a GUI code editor for the given path with robust error handling.
#     """
#     try:
#         target = target.resolve()
#     except Exception:
#         pass
#     if not target.exists():
#         return f"Error: Path does not exist: {target}"

#     # Use shlex.split for safety with user-provided commands
#     editor_cmd_str = os.getenv("EDITOR_CMD")
#     if editor_cmd_str:
#         cmd = shlex.split(editor_cmd_str) + [str(target)]
#         try:
#             subprocess.Popen(cmd)
#             return f"Launched via EDITOR_CMD: `{' '.join(cmd)}`"
#         except FileNotFoundError:
#             return f"Error: Command '{cmd[0]}' from EDITOR_CMD not found."
#         except Exception as e:
#             return f"Error launching via EDITOR_CMD: {e}"

#     # Try common editors, preferring VS Code's 'code' CLI
#     if shutil.which("code"):
#         try:
#             subprocess.Popen(["code", str(target)])
#             return f"Opened in VS Code: `code {target}`"
#         except Exception as e:
#             return f"Error launching VS Code: {e}"

#     # Fallback for macOS if 'code' is not in PATH
#     if sys.platform == "darwin":
#         try:
#             subprocess.Popen(["open", "-a", "Visual Studio Code", str(target)])
#             return 'Attempted to open in "Visual Studio Code" app.'
#         except Exception:
#             # Fallback to default app
#             try:
#                 subprocess.Popen(["open", str(target)])
#                 return f"Opened with default application via `open`."
#             except Exception as e:
#                 return f"Error using 'open': {e}"
    
#     return (
#         "No suitable editor found. For best results, install VS Code and ensure "
#         "the 'code' command is in your system's PATH, or set the EDITOR_CMD "
#         "environment variable (e.g., 'subl' for Sublime Text)."
#     )


# def button_key(prefix: str, path_like: str) -> str:
#     """Creates a stable, unique key for Streamlit widgets based on a path."""
#     digest = hashlib.md5(path_like.encode("utf-8", errors="ignore")).hexdigest()[:8]
#     return f"{prefix}_{digest}"