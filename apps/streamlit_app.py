# apps/streamlit_app.py
from __future__ import annotations

import os
import sys
import shlex
import shutil
import subprocess
import hashlib
from pathlib import Path

import streamlit as st

from tools.codefill import codefill_run, resolve_root_dir, _find_all_files  # type: ignore

st.set_page_config(page_title="Project Structure Builder", layout="wide")
st.title("📂 Project Structure Builder + LLM Code Filler")

# ----------------------------
# Sidebar options
# ----------------------------
with st.sidebar:
    st.subheader("⚙️ Options")
    base_dir = st.text_input(
        "Destination folder (base)",
        value=os.getenv("CODEFILL_FORCE_BASE_DIR", "/Users/pranavsaji/Downloads/Projects"),
        help="Where to create/find the root project folder.",
    )
    root_name = st.text_input(
        "Root project folder",
        value=os.getenv("CODEFILL_FORCE_ROOT_NAME", "Logistics-integrations-assistant"),
        help="Project folder under the base directory.",
    )
    mode = st.radio("If file exists", options=["overwrite", "skip"], index=0, horizontal=True)
    provider = st.selectbox("LLM provider", options=["groq", "openai"], index=0)
    st.caption(
        f"OPENAI_API_KEY set: {'✅' if os.getenv('OPENAI_API_KEY') else '❌'}  |  "
        f"GROQ_API_KEY set: {'✅' if os.getenv('GROQ_API_KEY') else '❌'}"
    )

# Compute a preview of the intended destination folder (even before running)
def _dest_preview() -> Path:
    try:
        # resolve_root_dir is authoritative after run; for preview use join+resolve
        return (Path(base_dir).expanduser() / root_name).resolve()
    except Exception:
        return Path(base_dir).expanduser().resolve()

# ----------------------------
# Helpers
# ----------------------------
def _ensure_dump_file(tmp_dir: Path, dump_text: str, dump_file) -> Path:
    """
    Persist the user's dump to a temp file for the filler.
    If a file is uploaded we keep the raw bytes; otherwise we encode the text as UTF-8.
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    p = tmp_dir / "dump.txt"
    if dump_file is not None:
        p.write_bytes(dump_file.getvalue())
    else:
        p.write_text(dump_text or "", encoding="utf-8")
    return p

def _lang_for(rel: str) -> str:
    r = rel.lower()
    if r.endswith(".py"): return "python"
    if r.endswith(".md"): return "markdown"
    if r.endswith(".yml") or r.endswith(".yaml"): return "yaml"
    if r.endswith(".json"): return "json"
    if r.endswith(".xml"): return "xml"
    if r.endswith(".csv"): return "csv"
    if r.endswith("dockerfile"): return "dockerfile"
    if r.endswith(".toml"): return "toml"
    if r.endswith(".ini"): return "ini"
    if r.endswith(".env") or r.endswith(".example"): return ""
    return ""

def _button_key(prefix: str, path_like: str) -> str:
    # Stable, safe key from path + prefix
    digest = hashlib.md5(path_like.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{prefix}_{digest}"

def _launch_editor(target: Path, editor_preference: str | None = None) -> str:
    """
    Best-effort launcher for VS Code or any GUI editor.
    - Honors EDITOR_CMD if set (e.g. "code -g").
    - Tries 'code', 'subl', 'atom', macOS 'open -a "Visual Studio Code"', Windows 'start', Linux 'xdg-open'.
    Returns a human-readable message.
    """
    try:
        target = target.resolve()
    except Exception:
        pass

    if not target.exists():
        return f"Path does not exist: {target}"

    attempted: list[list[str]] = []

    def _popen(cmd: list[str]) -> bool:
        attempted.append(cmd)
        try:
            subprocess.Popen(cmd)
            return True
        except Exception as e:
            st.warning(f"Launch error with {cmd}: {e}")
            return False

    editor_cmd = os.getenv("EDITOR_CMD") or editor_preference
    if editor_cmd:
        cmd = shlex.split(editor_cmd) + [str(target)]
        if _popen(cmd):
            return f"Launched via EDITOR_CMD: `{editor_cmd} {target}`"

    # VS Code CLI
    if shutil.which("code"):
        if _popen(["code", str(target)]):
            return f"Opened in VS Code: code {target}"

    # Sublime / Atom
    if shutil.which("subl"):
        if _popen(["subl", str(target)]):
            return f"Opened in Sublime Text: subl {target}"
    if shutil.which("atom"):
        if _popen(["atom", str(target)]):
            return f"Opened in Atom: atom {target}"

    # macOS
    if sys.platform == "darwin" and shutil.which("open"):
        if _popen(["open", "-a", "Visual Studio Code", str(target)]):
            return f'Opened in "Visual Studio Code": open -a "Visual Studio Code" {target}'
        if _popen(["open", str(target)]):
            return f"Opened with default app: open {target}"

    # Windows
    if os.name == "nt":
        try:
            attempted.append(["cmd", "/c", "start", "", str(target)])
            subprocess.Popen(["cmd", "/c", "start", "", str(target)], shell=True)
            return f"Opened with default app: start {target}"
        except Exception as e:
            return f"Failed to open on Windows: {e}"

    # Linux
    if shutil.which("xdg-open"):
        if _popen(["xdg-open", str(target)]):
            return f"Opened with default app: xdg-open {target}"

    # Final fallback
    return (
        "No suitable editor launcher found. "
        "Install VS Code and ensure `code` is on PATH, or set EDITOR_CMD "
        "(e.g. 'code -g').\n\nTried:\n- " + "\n- ".join(" ".join(c) for c in attempted)
    )

# ----------------------------
# Main UI
# ----------------------------
st.markdown("### Paste or upload your **dump**")

left, right = st.columns([0.6, 0.4], gap="large")
with left:
    dump_text = st.text_area("Dump text", height=300, placeholder="Paste the dump here…")
with right:
    dump_file = st.file_uploader("…or upload dump.txt / dump.md", type=["txt", "md"])

# Always allow opening the target folder (even before run)
dest_preview = _dest_preview()
col_open_now, col_run = st.columns([0.35, 0.65])
if col_open_now.button("🖥️ Open current destination in editor", key="open_dest_preview"):
    msg = _launch_editor(dest_preview)
    st.toast(msg)

run = col_run.button("Run code fill", type="primary", key="run_codefill")
results_container = st.container()
st.divider()

if run:
    try:
        # feed options to filler
        os.environ["CODEFILL_FORCE_BASE_DIR"] = base_dir
        os.environ["CODEFILL_FORCE_ROOT_NAME"] = root_name
        os.environ["CODEFILL_MODE"] = mode
        os.environ["LLM_PROVIDER"] = provider

        # Authoritative destination from helper, ensure absolute
        dest_root = resolve_root_dir(base_dir, root_name).resolve()
        dump_path = _ensure_dump_file(Path(".streamlit_tmp"), dump_text, dump_file)

        # Keep a copy of the dump in the destination for later runs (optional)
        try:
            dest_root.mkdir(parents=True, exist_ok=True)
            (dest_root / "dump.txt").write_bytes(dump_path.read_bytes())
        except Exception as e:
            st.warning(f"Could not copy dump into destination project: {e}")

        result = codefill_run(
            dump_file=dump_path,
            root_dir=dest_root,
            mode=mode,
            create_missing=True
        )

        with results_container:
            st.success(f"Code fill completed for {dest_root}")
            st.json(result)

        # Quick open project button (guaranteed absolute path)
        if st.button("🖥️ Open project in editor", key="open_project_after_run", type="secondary"):
            msg = _launch_editor(dest_root)
            st.toast(msg)

        st.subheader(f"🗂 Files in {dest_root}")
        for p in _find_all_files(dest_root):
            rel = p.relative_to(dest_root).as_posix()
            key_suffix = _button_key("openfile", rel)
            with st.expander(rel, expanded=False):
                topc, codec = st.columns([1.2, 8])
                if topc.button("Open in editor", key=f"btn_{key_suffix}"):
                    msg = _launch_editor(p)
                    st.toast(msg)
                try:
                    content = p.read_text(encoding="utf-8", errors="replace")
                except Exception as e:
                    content = f"<<unable to read>>: {e}"
                codec.code(content, language=_lang_for(rel))

    except Exception as e:
        st.error(f"Fill failed: {e}")
