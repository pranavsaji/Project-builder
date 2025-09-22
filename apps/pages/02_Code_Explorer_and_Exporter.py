"""
Streamlit page: Code Explorer & Exporter (v2, stateful & resilient)

Fixes & additions:
- Persist scan results and selection in session_state so removing one file doesn't clear everything.
- Per-file ✕ buttons to remove single items from the selection.
- DOCX export no longer crashes on binary/control chars (handled in tools/doc_export.py).
- New: Open in editor
  * Folder mode: Open base folder in editor + per-file Open buttons.
  * ZIP mode: Materialize selection to a temp workspace and open in editor.
- Refactored to use the centralized `launch_editor` utility.
"""
from __future__ import annotations

import io
import os
import re
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Dict, List

import streamlit as st

# Make project root importable (this file is /apps/pages/... so root is parents[2])
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Local and shared utils
from tools.file_harvester import harvest_folder
from tools.zip_utils import parse_zipfile
from tools.doc_export import build_markdown_document, build_docx_document, build_zip_of_sources
from tools.trashcan import permanent_delete, move_to_trash
from tools.ui_utils import launch_editor, button_key  # <-- IMPORTED from shared utility

st.set_page_config(page_title="Code Explorer & Exporter", page_icon="📦", layout="wide")
st.title("📦 Code Explorer & Exporter")

DEFAULT_BASE = os.path.expanduser("~/Downloads/Projects")

with st.expander("Defaults & tips", expanded=False):
    st.markdown(
        """
- **Folder mode**: Provide a base path. We'll walk it recursively and collect text files (skipping large/binary by default).
- **Zip mode**: Upload a `.zip`—we parse it in-memory without writing to disk.
- **Exports**: Combined Markdown (copy-pastable), optional `.docx` (needs `python-docx`), and `.zip` of sources.
- **Excludes** (by default): `.git`, `venv`, `.venv`, `node_modules`, `__pycache__`, `.pytest_cache`, `.mypy_cache`,
  `.ruff_cache`, `.tox`, `dist`, `build`, `.cache`.
- If you change filters, hit **Scan Folder** again to refresh results.
- **New:** Use the **Open in editor** buttons to jump straight into VS Code (or your default editor).
        """
    )

tab1, tab2, tab3 = st.tabs(["📂 From Folder Path", "🗜️ From Uploaded ZIP", "🗑️ Danger Zone"])

# ----------------------------
# Sidebar controls (common)
# ----------------------------
with st.sidebar:
    st.header("Filters")
    max_kb = st.slider("Max file size (KB)", min_value=50, max_value=4096, value=512, step=50)
    include_hidden = st.checkbox("Include hidden files (e.g., .env)", value=False)
    st.caption("Hidden files are excluded by default.")

    st.subheader("Exclusions")
    default_excludes = [
        ".git", "venv", ".venv", "node_modules", "__pycache__", ".pytest_cache",
        ".mypy_cache", ".ruff_cache", ".tox", ".cache", "dist", "build"
    ]
    excludes_text = st.text_area(
        "Exclude paths containing these (comma-separated)",
        value=", ".join(default_excludes),
        height=100,
    )
    exclude_tokens = [t.strip() for t in excludes_text.split(",") if t.strip()]


# ---- Helpers ----
def _rerun():
    """Helper to rerun the Streamlit app, handling legacy versions."""
    st.rerun()

def _materialize_selection_to_temp(files: List[Dict]) -> Path:
    """
    Writes selected in-memory files (from a ZIP) to a temporary directory
    so they can be opened in an external editor.
    Returns the path to the temporary directory.
    """
    base = Path(tempfile.gettempdir()) / "project_builder_zip_open" / str(int(time.time()))
    for f in files:
        rel = (f.get("rel_path") or f.get("path") or "file.txt").lstrip("/\\")
        dest = base / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = f.get("content", "")
        # Write as UTF-8, replacing any invalid characters
        dest.write_text(content, encoding="utf-8", errors="replace")
    return base

def _safe_project_name(name: str) -> str:
    """Sanitizes a project name for use in filenames."""
    s = (name or "project").strip().strip("/\\")
    # Allow letters, numbers, dot, underscore, hyphen; replace others
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)


# ----------------------------
# Tab 1: From folder path
# ----------------------------
with tab1:
    st.subheader("Folder Scanner")

    # Initialize session_state for this tab
    st.session_state.setdefault("cex_last_base", "")
    st.session_state.setdefault("cex_scan_results", [])
    st.session_state.setdefault("cex_selected", [])

    base_path = st.text_input("Base folder path", value=st.session_state.get("cex_last_base") or DEFAULT_BASE)

    col_a, col_b, col_c = st.columns(3)
    scan_clicked = col_a.button("Scan Folder", type="primary", use_container_width=True)
    reset_sel_clicked = col_b.button("Reset Selection", use_container_width=True)

    # If base path changes, clear the previous results to avoid confusion
    if base_path != st.session_state["cex_last_base"]:
        st.session_state.update(cex_last_base=base_path, cex_scan_results=[], cex_selected=[])

    base = Path(base_path).expanduser().resolve()

    if scan_clicked:
        if not base.is_dir():
            st.error(f"Path not found or not a directory: `{base}`")
        else:
            with st.spinner("Walking folder and reading files..."):
                results = harvest_folder(
                    base_dir=base,
                    max_bytes=max_kb * 1024,
                    exclude_tokens=exclude_tokens,
                    include_hidden=include_hidden,
                )
            st.session_state.cex_scan_results = results
            st.session_state.cex_selected = list(results)  # Start with all files selected
            _rerun()

    if reset_sel_clicked and st.session_state.cex_scan_results:
        st.session_state.cex_selected = list(st.session_state.cex_scan_results)
        _rerun()

    selected = st.session_state.cex_selected
    if st.session_state.cex_scan_results:
        st.success(f"Scanned {len(st.session_state.cex_scan_results)} file(s). Currently selected: **{len(selected)}**")

        if st.button("🖥️ Open base folder in editor", key="open_base_editor"):
            st.toast(launch_editor(base))

        st.markdown("#### Selected Files")
        if not selected:
            st.info("No files selected. Click **Reset Selection** to re-select all scanned files.")
        else:
            to_remove_indices = []
            for i, f in enumerate(selected):
                rel = f.get("rel_path", "unknown_file")
                c1, c2, c3, c4, c5 = st.columns([0.1, 0.4, 0.2, 0.15, 0.15])
                if c1.button("✕", key=button_key("rm", rel), help="Remove from selection"):
                    to_remove_indices.append(i)
                c2.write(rel)
                c3.caption(f"{f.get('size', 0)} bytes | `{f.get('language', 'text')}`")
                with c4.popover("Preview"):
                    st.code(f.get("content", ""), language=f.get("language"))
                if c5.button("Open", key=button_key("open", rel)):
                    st.toast(launch_editor(base / rel))

            if to_remove_indices:
                st.session_state.cex_selected = [f for i, f in enumerate(selected) if i not in to_remove_indices]
                _rerun()

        st.divider()
        st.subheader("📄 Export Selection")
        col1, col2, col3 = st.columns(3)
        project_name = _safe_project_name(base.name)

        with col1:
            md_doc = build_markdown_document(selected, title="Code Export — Folder Mode", base_path=str(base))
            st.download_button("⬇️ Download Markdown", md_doc, f"{project_name}_export.md", "text/markdown", use_container_width=True)
        with col2:
            docx_bytes = build_docx_document(selected, title="Code Export — Folder Mode")
            if docx_bytes:
                st.download_button("⬇️ Download .docx", docx_bytes, f"{project_name}_export.docx", use_container_width=True)
            else:
                st.info("Install `python-docx` to enable .docx export.")
        with col3:
            zip_bytes = build_zip_of_sources(selected)
            st.download_button("⬇️ Download .zip", zip_bytes, f"{project_name}_sources.zip", "application/zip", use_container_width=True)

# ----------------------------
# Tab 2: From uploaded zip
# ----------------------------
with tab2:
    st.subheader("ZIP Uploader")
    upl = st.file_uploader("Upload a .zip containing your project", type=["zip"])
    if upl:
        try:
            with st.spinner("Reading zip contents..."):
                zf = zipfile.ZipFile(io.BytesIO(upl.read()))
                results_zip = parse_zipfile(zf, max_bytes=max_kb * 1024, exclude_tokens=exclude_tokens, include_hidden=include_hidden)

            st.success(f"Found {len(results_zip)} textual file(s) in zip.")
            
            all_paths = [r["rel_path"] for r in results_zip]
            selected_paths = st.multiselect("Select files to include in export", options=all_paths, default=all_paths)
            filtered_zip = [r for r in results_zip if r["rel_path"] in selected_paths]

            if st.button("🖥️ Open selection in editor (temp folder)", key="open_zip_selection"):
                temp_dir = _materialize_selection_to_temp(filtered_zip)
                st.toast(launch_editor(temp_dir))
                st.caption(f"Temp workspace created at: `{temp_dir}`")

            with st.expander("🔎 Preview Selected Files", expanded=False):
                for r in filtered_zip:
                    with st.container(border=True):
                        st.markdown(f"**{r['rel_path']}** ({r['size']} bytes)")
                        st.code(r["content"], language=r.get("language") or "text")
            
            st.divider()
            st.subheader("📄 Export Selection")
            col1, col2, col3 = st.columns(3)
            zip_project_name = _safe_project_name(Path(upl.name).stem)

            with col1:
                md_zip = build_markdown_document(filtered_zip, title="Code Export — ZIP Mode", base_path=upl.name)
                st.download_button("⬇️ Download Markdown", md_zip, f"{zip_project_name}_export.md", "text/markdown", use_container_width=True)
            with col2:
                docx_zip = build_docx_document(filtered_zip, title="Code Export — ZIP Mode")
                if docx_zip:
                    st.download_button("⬇️ Download .docx", docx_zip, f"{zip_project_name}_export.docx", use_container_width=True)
                else:
                    st.info("Install `python-docx` to enable.")
            with col3:
                zip_sources = build_zip_of_sources(filtered_zip)
                st.download_button("⬇️ Download .zip", zip_sources, f"{zip_project_name}_sources.zip", "application/zip", use_container_width=True)

        except Exception as e:
            st.error("Failed to process ZIP file.")
            st.exception(e)

# ----------------------------
# Tab 3: Danger Zone
# ----------------------------
with tab3:
    st.subheader("🗑️ Danger Zone File Manager")
    st.warning("⚠️ Actions here are permanent or move files. Proceed with caution.")
    
    danger_base_path = st.text_input("Base folder for file operations", value=DEFAULT_BASE, key="danger_base")
    danger_base = Path(danger_base_path).expanduser().resolve()

    if not danger_base.is_dir():
        st.error(f"Path not found or not a directory: `{danger_base}`")
    else:
        try:
            children = [p.name for p in danger_base.iterdir() if not p.name.startswith('.')]
            selected_items = st.multiselect(
                "Select files or folders to manage",
                options=sorted(children),
                key="danger_zone_selection"
            )

            if selected_items:
                col_safe, col_perm = st.columns(2)
                
                if col_safe.button("Move to `.trash/` (Safe)", use_container_width=True):
                    with st.spinner("Moving items to trash..."):
                        results = move_to_trash(danger_base, selected_items)
                        st.success(f"Moved {len(results)} items to a new folder inside `{danger_base / '.trash'}`")
                        st.rerun()

                if col_perm.button("🔥 Delete Permanently", type="primary", use_container_width=True):
                    st.session_state.show_perm_delete_confirm = True

                if st.session_state.get("show_perm_delete_confirm"):
                    with st.form("confirm_delete"):
                        st.error("This action is irreversible. Are you absolutely sure?")
                        confirm_text = st.text_input("Type `delete permanently` to confirm:")
                        submitted = st.form_submit_button("Confirm Permanent Deletion")

                        if submitted:
                            if confirm_text == "delete permanently":
                                with st.spinner("Permanently deleting items..."):
                                    results = permanent_delete(danger_base, selected_items)
                                    st.success(f"Permanently deleted {len(results)} items.")
                                    st.session_state.show_perm_delete_confirm = False
                                    st.rerun()
                            else:
                                st.warning("Confirmation text did not match. Deletion cancelled.")
                                st.session_state.show_perm_delete_confirm = False
                                st.rerun()

        except Exception as e:
            st.error(f"An error occurred while listing directory contents: {e}")

# """
# Streamlit page: Code Explorer & Exporter (v2, stateful & resilient)

# Fixes & additions:
# - Persist scan results and selection in session_state so removing one file doesn't clear everything.
# - Per-file ✕ buttons to remove single items from the selection.
# - DOCX export no longer crashes on binary/control chars (handled in tools/doc_export.py).
# - New: Open in editor
#   * Folder mode: Open base folder in editor + per-file Open buttons.
#   * ZIP mode: Materialize selection to a temp workspace and open in editor.
# """
# from __future__ import annotations

# import io
# import os
# import sys
# import shlex
# import time
# import shutil
# import zipfile
# import tempfile
# import subprocess
# from pathlib import Path
# from typing import Dict, List

# import streamlit as st

# # Make project root importable (this file is /apps/pages/... so root is parents[2])
# ROOT = Path(__file__).resolve().parents[2]
# if str(ROOT) not in sys.path:
#     sys.path.insert(0, str(ROOT))

# # Local utils
# from tools.file_harvester import harvest_folder, guess_language
# from tools.zip_utils import parse_zipfile
# from tools.doc_export import build_markdown_document, build_docx_document, build_zip_of_sources
# from tools.trashcan import move_to_trash, permanent_delete, list_immediate_children

# st.set_page_config(page_title="Code Explorer & Exporter", page_icon="📦", layout="wide")
# st.title("📦 Code Explorer & Exporter")

# DEFAULT_BASE = "/Users/pranavsaji/Downloads/Projects/"

# with st.expander("Defaults & tips", expanded=False):
#     st.markdown(
#         """
# - **Folder mode**: Provide a base path. We'll walk it recursively and collect text files (skipping large/binary by default).
# - **Zip mode**: Upload a `.zip`—we parse it in-memory without writing to disk.
# - **Exports**: combined Markdown (copy-pastable), optional `.docx` (needs `python-docx`), and `.zip` of sources.
# - **Excludes** (by default): `.git`, `venv`, `.venv`, `node_modules`, `__pycache__`, `.pytest_cache`, `.mypy_cache`,
#   `.ruff_cache`, `.tox`, `dist`, `build`, `.cache`.
# - If you change filters, hit **Rescan** to refresh results.
# - **New:** Use the **Open in editor** buttons to jump straight into VS Code (or your default editor).
#         """
#     )

# tab1, tab2 = st.tabs(["📂 From folder path", "🗜️ From uploaded zip"])

# # ----------------------------
# # Sidebar controls (common)
# # ----------------------------
# with st.sidebar:
#     st.header("Filters")
#     max_kb = st.slider("Max file size (KB)", min_value=50, max_value=4096, value=256, step=50)
#     include_hidden = st.checkbox("Include hidden files (.*)", value=False)
#     st.caption("Hidden files are excluded unless you tick the box above.")

#     st.subheader("Exclusions (substring match, comma-separated)")
#     default_excludes = [
#         ".git", "venv", ".venv", "node_modules", "__pycache__", ".pytest_cache",
#         ".mypy_cache", ".ruff_cache", ".tox", ".cache", "dist", "build"
#     ]
#     excludes_text = st.text_area(
#         "Exclude paths containing any of these tokens",
#         value=", ".join(default_excludes),
#         height=100,
#     )
#     exclude_tokens = [t.strip() for t in excludes_text.split(",") if t.strip()]


# # Helpers
# def _rerun():
#     try:
#         st.rerun()
#     except Exception:
#         st.experimental_rerun()  # legacy


# def _launch_editor(target: Path, editor_preference: str | None = None) -> str:
#     """
#     Best-effort launcher for VS Code or any GUI editor.
#     Honors EDITOR_CMD if set (e.g., 'code -g').
#     """
#     if not target.exists():
#         return f"Path does not exist: {target}"

#     def _popen(cmd: list[str]) -> bool:
#         try:
#             subprocess.Popen(cmd)
#             return True
#         except Exception as e:
#             st.warning(f"Launch error with {cmd}: {e}")
#             return False

#     editor_cmd = os.getenv("EDITOR_CMD") or editor_preference
#     if editor_cmd:
#         cmd = shlex.split(editor_cmd) + [str(target)]
#         if _popen(cmd):
#             return f"Launched via EDITOR_CMD: `{editor_cmd}`"

#     if shutil.which("code"):
#         if _popen(["code", str(target)]):
#             return "Opened in VS Code (code)."
#     if shutil.which("subl"):
#         if _popen(["subl", str(target)]):
#             return "Opened in Sublime Text (subl)."
#     if shutil.which("atom"):
#         if _popen(["atom", str(target)]):
#             return "Opened in Atom."

#     if sys.platform == "darwin" and shutil.which("open"):
#         if _popen(["open", "-a", "Visual Studio Code", str(target)]):
#             return 'Opened in "Visual Studio Code" (open -a).'
#         if _popen(["open", str(target)]):
#             return "Opened with default application (open)."

#     if os.name == "nt":
#         try:
#             subprocess.Popen(["cmd", "/c", "start", "", str(target)], shell=True)
#             return "Opened with default application (start)."
#         except Exception as e:
#             return f"Failed to open on Windows: {e}"

#     if shutil.which("xdg-open"):
#         if _popen(["xdg-open", str(target)]):
#             return "Opened with default application (xdg-open)."

#     return (
#         "No suitable editor launcher found. Install VS Code and ensure `code` is on PATH, "
#         "or set EDITOR_CMD (e.g. 'code -g')."
#     )


# def _materialize_selection_to_temp(files: List[Dict]) -> Path:
#     """
#     Write selected in-memory files to a temporary workspace so they can be opened in an editor.
#     Returns the temp directory path.
#     """
#     base = Path(tempfile.gettempdir()) / "project_builder_zip_open" / str(int(time.time()))
#     for f in files:
#         rel = (f.get("rel_path") or f.get("path") or f.get("name") or "file.txt").lstrip("/\\")
#         dest = base / rel
#         dest.parent.mkdir(parents=True, exist_ok=True)
#         content = f.get("content", "")
#         try:
#             dest.write_text(content, encoding="utf-8", errors="replace")
#         except Exception:
#             # As a last resort, write bytes from UTF-8 replacement
#             dest.write_bytes(content.encode("utf-8", errors="replace"))
#     return base


# def _safe_project_name(name: str) -> str:
#     """Sanitize a project name for filenames."""
#     import re
#     s = (name or "").strip().strip("/\\")
#     if not s:
#         s = "project"
#     # allow letters, numbers, dot, underscore, hyphen; replace others with underscore
#     return re.sub(r"[^A-Za-z0-9._-]+", "_", s)


# # ----------------------------
# # Tab 1: From folder path
# # ----------------------------
# with tab1:
#     st.subheader("Folder scanner")

#     # Initialize session_state buckets
#     if "cex_last_base" not in st.session_state:
#         st.session_state["cex_last_base"] = ""
#     if "cex_scan_results" not in st.session_state:
#         st.session_state["cex_scan_results"] = []     # type: List[Dict]
#     if "cex_selected" not in st.session_state:
#         st.session_state["cex_selected"] = []         # type: List[Dict]

#     base_path = st.text_input("Base folder path", value=st.session_state.get("cex_last_base") or DEFAULT_BASE)

#     # Buttons
#     col_a, col_b, col_c, col_d = st.columns([1, 1, 1, 4])
#     scan_clicked = col_a.button("Scan folder", type="primary", use_container_width=True)
#     rescan_clicked = col_b.button("Rescan", use_container_width=True)
#     reset_sel_clicked = col_c.button("Reset selection", use_container_width=True)

#     # If base path changed, clear previous results/selection
#     if base_path != st.session_state["cex_last_base"]:
#         st.session_state["cex_last_base"] = base_path
#         st.session_state["cex_scan_results"] = []
#         st.session_state["cex_selected"] = []

#     base = Path(base_path).expanduser().resolve()

#     # Perform scan only when requested
#     if scan_clicked or rescan_clicked:
#         if not base.exists() or not base.is_dir():
#             st.error(f"Path not found or not a directory: `{base}`")
#         else:
#             with st.spinner("Walking and reading files..."):
#                 results = harvest_folder(
#                     base_dir=base,
#                     max_bytes=max_kb * 1024,
#                     exclude_tokens=exclude_tokens,
#                     include_hidden=include_hidden,
#                 )
#             st.session_state["cex_scan_results"] = results
#             # Start with everything selected on (re)scan
#             st.session_state["cex_selected"] = list(results)
#             _rerun()

#     # Reset selection to all scanned
#     if reset_sel_clicked and st.session_state["cex_scan_results"]:
#         st.session_state["cex_selected"] = list(st.session_state["cex_scan_results"])
#         _rerun()

#     results = st.session_state["cex_scan_results"]
#     selected = st.session_state["cex_selected"]

#     if results:
#         st.success(f"Scanned {len(results)} file(s). Currently selected: **{len(selected)}**")

#         # Quick open base in editor
#         if st.button("🖥️ Open base folder in editor", key="open_base_editor", type="secondary"):
#             msg = _launch_editor(base)
#             st.toast(msg)

#         # Show selection with per-item remove & open buttons
#         st.markdown("#### Selected files")
#         if not selected:
#             st.info("No files selected. Click **Reset selection** to select all again.")
#         else:
#             to_remove_idx: List[int] = []
#             for i, f in enumerate(selected):
#                 rel = f.get("rel_path") or f.get("path") or f.get("name")
#                 size = f.get("size")
#                 lang = f.get("language") or "text"
#                 c1, c2, c3, c4, c5 = st.columns([0.6, 5.6, 2, 1.4, 1.8])
#                 with c1:
#                     if st.button("✕", key=f"rm_{i}_{rel}"):
#                         to_remove_idx.append(i)
#                 with c2:
#                     st.write(rel)
#                 with c3:
#                     st.caption(f"{size} bytes — `{lang}`")
#                 with c4:
#                     with st.popover("Preview", use_container_width=True):
#                         st.code(f.get("content", ""), language=lang)
#                 with c5:
#                     # Open in editor (on-disk file)
#                     if st.button("Open", key=f"open_{i}_{rel}"):
#                         target = base / rel
#                         if target.exists():
#                             msg = _launch_editor(target)
#                             st.toast(msg)
#                         else:
#                             st.warning("File no longer exists on disk.")

#             if to_remove_idx:
#                 for idx in sorted(to_remove_idx, reverse=True):
#                     selected.pop(idx)
#                 st.session_state["cex_selected"] = selected
#                 _rerun()

#         # Exports (Markdown / DOCX / ZIP) from the current selection
#         st.divider()
#         st.subheader("📄 Export")
#         col1, col2, col3 = st.columns(3)
#         filtered = selected  # alias for prior naming

#         combined_md = build_markdown_document(filtered, title="Code Export — Folder Mode", base_path=str(base))

#         # derive project name from folder and use it for .docx filename
#         project_name = _safe_project_name(base.name)
#         docx_filename = f"{project_name}_code.docx"

#         with col1:
#             st.download_button(
#                 "⬇️ Download Markdown",
#                 data=combined_md.encode("utf-8"),
#                 file_name="code-export.md",
#                 mime="text/markdown",
#                 use_container_width=True,
#             )
#         with col2:
#             docx_bytes = build_docx_document(filtered, title="Code Export — Folder Mode")
#             if docx_bytes is not None:
#                 st.download_button(
#                     "⬇️ Download .docx",
#                     data=docx_bytes,
#                     file_name=docx_filename,  # <-- project-based name
#                     mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
#                     use_container_width=True,
#                 )
#             else:
#                 st.info("`.docx` export requires `python-docx`. Install it to enable this button.")
#         with col3:
#             zip_bytes = build_zip_of_sources(filtered)
#             st.download_button(
#                 "⬇️ Download sources (.zip)",
#                 data=zip_bytes,
#                 file_name="sources-selected.zip",
#                 mime="application/zip",
#                 use_container_width=True,
#             )

#         # Optional large preview
#         with st.expander("🔎 Full preview (selected files)", expanded=False):
#             for r in filtered:
#                 with st.container(border=True):
#                     st.markdown(f"**{r['rel_path']}**  \n_Size:_ {r['size']} bytes  \n_Language:_ `{r['language']}`")
#                     st.code(r["content"], language=r["language"] or "text")
#     else:
#         st.info("Enter a base folder and click **Scan folder**.")

# # ----------------------------
# # Tab 2: From uploaded zip
# # ----------------------------
# with tab2:
#     st.subheader("ZIP uploader")
#     upl = st.file_uploader("Upload a .zip containing your project", type=["zip"])
#     if upl:
#         try:
#             with st.spinner("Reading zip contents..."):
#                 zf = zipfile.ZipFile(io.BytesIO(upl.read()))
#                 results_zip = parse_zipfile(
#                     zf,
#                     max_bytes=max_kb * 1024,
#                     exclude_tokens=exclude_tokens,
#                     include_hidden=include_hidden,
#                 )
#             st.success(f"Found {len(results_zip)} file(s) in zip.")
#         except Exception as e:
#             st.exception(e)
#             results_zip = []

#         if results_zip:
#             all_paths = [r["rel_path"] for r in results_zip]
#             selected_zip = st.multiselect(
#                 "Select files to include in export",
#                 options=all_paths,
#                 default=all_paths,
#                 key="zip_select",
#             )
#             selz = set(selected_zip)
#             filtered_zip = [r for r in results_zip if r["rel_path"] in selz]

#             # Open selection in editor by materializing to a temp workspace
#             if st.button("🖥️ Open selection in editor (temp folder)", key="open_zip_selection"):
#                 temp_dir = _materialize_selection_to_temp(filtered_zip)
#                 msg = _launch_editor(temp_dir)
#                 st.toast(msg)
#                 st.caption(f"Temp workspace: `{temp_dir}`")

#             with st.expander("🔎 Preview files", expanded=False):
#                 for r in filtered_zip:
#                     with st.container(border=True):
#                         st.markdown(f"**{r['rel_path']}**  \n_Size:_ {r['size']} bytes  \n_Language:_ `{r['language']}`")
#                         st.code(r["content"], language=r["language"] or "text")

#             combined_md_zip = build_markdown_document(filtered_zip, title="Code Export — ZIP Mode", base_path=upl.name)
#             st.subheader("📄 Combined document (Markdown)")
#             st.text_area("Copy-pastable document", value=combined_md_zip, height=300, key="md_zip")

#             # derive project name from uploaded zip filename (stem) and use it for .docx
#             zip_project_name = _safe_project_name(Path(upl.name).stem)
#             zip_docx_filename = f"{zip_project_name}_code.docx"

#             col1, col2, col3 = st.columns(3)
#             with col1:
#                 st.download_button(
#                     "⬇️ Download Markdown",
#                     data=combined_md_zip.encode("utf-8"),
#                     file_name="code-export-from-zip.md",
#                     mime="text/markdown",
#                     use_container_width=True,
#                 )
#             with col2:
#                 docx_bytes_zip = build_docx_document(filtered_zip, title="Code Export — ZIP Mode")
#                 if docx_bytes_zip is not None:
#                     st.download_button(
#                         "⬇️ Download .docx",
#                         data=docx_bytes_zip,
#                         file_name=zip_docx_filename,  # <-- project-based name
#                         mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
#                         use_container_width=True,
#                     )
#                 else:
#                     st.info("`.docx` export requires `python-docx`. Install it to enable this button.")
#             with col3:
#                 zip_bytes_zip = build_zip_of_sources(filtered_zip)
#                 st.download_button(
#                     "⬇️ Download sources (.zip)",
#                     data=zip_bytes_zip,
#                     file_name="sources-selected-from-zip.zip",
#                     mime="application/zip",
#                     use_container_width=True,
#                 )
