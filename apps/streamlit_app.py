# apps/streamlit_app.py
from __future__ import annotations

# ---------- import bootstrap (make project root importable) ----------
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # repo root
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# --------------------------------------------------------------------

import os
import streamlit as st

# Load .env early so key badges reflect env even before running
try:
    from dotenv import load_dotenv, find_dotenv  # type: ignore
    load_dotenv(find_dotenv(usecwd=True) or (ROOT / ".env"), override=False)
except Exception:
    pass

# Import shared utilities
from tools.codefill import codefill_run, resolve_root_dir, _find_all_files
from tools.ui_utils import launch_editor, button_key
from tools.file_harvester import guess_language


st.set_page_config(page_title="Project Structure Builder", layout="wide")
st.title("📂 Project Builder + LLM Code Filler")

# ---- Sidebar Configuration ----
with st.sidebar:
    st.subheader("⚙️ Options")

    base_dir = st.text_input(
        "Destination folder (base)",
        value=os.getenv("CODEFILL_FORCE_BASE_DIR", str(Path.home() / "Downloads" / "Projects")),
    )
    root_name = st.text_input(
        "Root project folder",
        value=os.getenv("CODEFILL_FORCE_ROOT_NAME", "my-new-project"),
    )
    mode = st.radio("If file exists", options=["overwrite", "skip"], index=0, horizontal=True)

    provider = st.selectbox("LLM provider", options=["groq", "openai"], index=0)

    st.subheader("LLM Reliability")
    # These names match tools/llm_client.py
    st.number_input("LLM throttle per request (ms)", key="LLM_THROTTLE_MS", min_value=0, value=int(os.getenv("LLM_THROTTLE_MS", "1200")))
    st.number_input("LLM max retries (on 429/5xx)", key="LLM_MAX_RETRIES", min_value=0, value=int(os.getenv("LLM_MAX_RETRIES", "5")))
    st.number_input("LLM backoff base (ms)", key="LLM_BACKOFF_BASE_MS", min_value=0, value=int(os.getenv("LLM_BACKOFF_BASE_MS", "800")))
    st.number_input("LLM backoff max (ms)", key="LLM_BACKOFF_MAX_MS", min_value=0, value=int(os.getenv("LLM_BACKOFF_MAX_MS", "8000")))
    st.number_input("LLM prompt max chars (cap)", key="LLM_MAX_CHARS", min_value=8000, value=int(os.getenv("LLM_MAX_CHARS", "90000")))

    # Key badges
    openai_ok = bool(os.getenv("OPENAI_API_KEY"))
    groq_ok = bool(os.getenv("GROQ_API_KEY"))
    st.caption(
        f"OPENAI_API_KEY set: {'✅' if openai_ok else '❌'}  |  "
        f"GROQ_API_KEY set: {'✅' if groq_ok else '❌'}"
    )

# ---- Main UI ----
st.markdown("### Paste or upload your project dump")
dump_text = st.text_area(
    "Paste the project structure, file contents, etc.",
    height=350,
    placeholder="Paste dump here…",
)

dest_preview = resolve_root_dir(base_dir, root_name)
st.caption(f"Project will be generated in: `{dest_preview}`")

# Open destination in VS Code (works even without `code` CLI)
col_open_now, col_run = st.columns([0.38, 0.62])
if col_open_now.button("🖥️ Open in VS Code", key=button_key("open_preview_in_vscode")):
    ok, msg = launch_editor(dest_preview, prefer="vscode")
    (st.success if ok else st.error)(msg)

run = col_run.button("✨ Generate Project", type="primary", key=button_key("run_codefill"))
st.divider()

# Session log buffer
if "log_messages" not in st.session_state:
    st.session_state.log_messages = []

log_container = st.container()

if run:
    # Wire env for the runner + LLM client
    os.environ["CODEFILL_FORCE_BASE_DIR"] = base_dir
    os.environ["CODEFILL_FORCE_ROOT_NAME"] = root_name
    os.environ["CODEFILL_MODE"] = mode
    os.environ["LLM_PROVIDER"] = provider

    # Pass sidebar reliability settings into env
    for key in ["LLM_THROTTLE_MS", "LLM_MAX_RETRIES", "LLM_BACKOFF_BASE_MS", "LLM_BACKOFF_MAX_MS", "LLM_MAX_CHARS"]:
        os.environ[key] = str(st.session_state.get(key, os.getenv(key, "")))

    # Optional: centralize logs from client/normalizer
    os.environ.setdefault("LLM_LOG_FILE", str(ROOT / "logs" / "llm_calls.log"))
    Path(os.environ["LLM_LOG_FILE"]).parent.mkdir(parents=True, exist_ok=True)

    tmp_dir = ROOT / ".streamlit_tmp"
    tmp_dir.mkdir(exist_ok=True)
    dump_path = tmp_dir / "dump.txt"
    dump_path.write_text(dump_text or "", encoding="utf-8")

    if not dump_path.read_text(encoding="utf-8").strip():
        st.error("Dump input is empty. Please paste a dump.")
    else:
        # Robust logging: stash messages in session state
        st.session_state.log_messages = []

        def ui_log(m: str) -> None:
            st.session_state.log_messages.append(m)

        result = None

        with log_container:
            with st.expander("🪵 Run Logs", expanded=True):
                log_placeholder = st.empty()
                log_placeholder.text_area(
                    "Logs",
                    "Starting project generation…",
                    height=240,
                    key="live_log_display",
                    label_visibility="collapsed",
                )

                try:
                    dest_root = resolve_root_dir(base_dir, root_name)
                    with st.spinner("Parsing and generating project…"):
                        result = codefill_run(
                            dump_file=dump_path,
                            root_dir=dest_root,
                            mode=mode,
                            logger=ui_log,
                        )

                    # Final flush
                    log_placeholder.text_area(
                        "Logs",
                        "\n".join(st.session_state.log_messages),
                        height=300,
                        key="final_log_display_success",
                        label_visibility="collapsed",
                    )

                except Exception as e:
                    ui_log(f"FATAL ERROR: {e}")
                    log_placeholder.text_area(
                        "Logs",
                        "\n".join(st.session_state.log_messages),
                        height=300,
                        key="final_log_display_error",
                        label_visibility="collapsed",
                    )
                    st.error(f"An unexpected error occurred during the run: {e}")

        # Results panel
        if result:
            st.success(f"Project generation complete for `{dest_root}`")

            # Quick actions row
            a, b = st.columns([0.4, 0.6])
            with a:
                if st.button("🖥️ Open project in VS Code", key=button_key("open_after_run")):
                    ok, msg = launch_editor(dest_root, prefer="vscode")
                    (st.success if ok else st.error)(msg)
            with b:
                st.code(f"code {dest_root}", language="bash")

            # Summary (counts)
            counts = result.get("count", {})
            if counts:
                st.subheader("📊 Summary")
                st.json(counts)

            # File browser
            st.subheader(f"🗂️ Generated Files in `{dest_root}`")
            try:
                files = sorted(_find_all_files(dest_root))
            except Exception as e:
                files = []
                st.error(f"Could not list files: {e}")

            for p in files:
                rel = p.relative_to(dest_root).as_posix()
                with st.expander(rel):
                    try:
                        content = p.read_text(encoding="utf-8", errors="replace")
                        lang = guess_language(p) or "text"
                        st.code(content, language=lang)
                    except Exception as e:
                        st.error(f"Could not display file: {e}")

# # apps/streamlit_app.py
# from __future__ import annotations

# import os
# from pathlib import Path

# import streamlit as st

# # Import shared utilities
# from tools.codefill import codefill_run, resolve_root_dir, _find_all_files
# from tools.ui_utils import launch_editor, button_key
# from tools.file_harvester import guess_language

# st.set_page_config(page_title="Project Structure Builder", layout="wide")
# st.title("📂 Project Builder + LLM Code Filler")

# # ---- Sidebar Configuration ----
# with st.sidebar:
#     st.subheader("⚙️ Options")
#     base_dir = st.text_input("Destination folder (base)", value=os.getenv("CODEFILL_FORCE_BASE_DIR", "/tmp/projects"))
#     root_name = st.text_input("Root project folder", value=os.getenv("CODEFILL_FORCE_ROOT_NAME", "my-new-project"))
#     mode = st.radio("If file exists", options=["overwrite", "skip"], index=0, horizontal=True)
#     provider = st.selectbox("LLM provider", options=["groq", "openai"], index=0)

#     st.subheader("LLM Reliability")
#     st.number_input("LLM throttle per request (ms)", key="LLM_MIN_DELAY_MS", min_value=0, value=1200)
#     st.number_input("LLM max retries (on 429/5xx)", key="LLM_MAX_RETRIES", min_value=0, value=4)
#     st.number_input("LLM backoff base (ms)", key="LLM_BACKOFF_BASE_MS", min_value=0, value=800)
#     st.number_input("LLM backoff max (ms)", key="LLM_BACKOFF_MAX_MS", min_value=0, value=8000)
#     st.number_input("Cap LLM max_tokens", key="LLM_MAX_TOKENS", min_value=512, value=8000)
    
#     st.caption(
#         f"OPENAI_API_KEY set: {'✅' if os.getenv('OPENAI_API_KEY') else '❌'}  |  "
#         f"GROQ_API_KEY set: {'✅' if os.getenv('GROQ_API_KEY') else '❌'}"
#     )

# # ---- Main UI ----
# st.markdown("### Paste or upload your project dump")
# dump_text = st.text_area("Paste the project structure, file contents, etc.", height=350, placeholder="Paste dump here...")

# dest_preview = resolve_root_dir(base_dir, root_name)
# st.caption(f"Project will be generated in: `{dest_preview}`")

# col_open_now, col_run = st.columns([0.35, 0.65])
# if col_open_now.button("🖥️ Open destination folder", key="open_dest_preview"):
#     st.toast(launch_editor(dest_preview))

# run = col_run.button("✨ Generate Project", type="primary", key="run_codefill")
# st.divider()

# if "log_messages" not in st.session_state:
#     st.session_state.log_messages = []

# # This placeholder will be populated with the log area when the run starts
# log_container = st.container()

# if run:
#     # Set environment variables for the codefill runner
#     os.environ["CODEFILL_FORCE_BASE_DIR"] = base_dir
#     os.environ["CODEFILL_FORCE_ROOT_NAME"] = root_name
#     os.environ["CODEFILL_MODE"] = mode
#     os.environ["LLM_PROVIDER"] = provider
#     for key in ["LLM_MIN_DELAY_MS", "LLM_MAX_RETRIES", "LLM_BACKOFF_BASE_MS", "LLM_BACKOFF_MAX_MS", "LLM_MAX_TOKENS"]:
#         os.environ[key] = str(st.session_state.get(key, 0))

#     tmp_dir = Path(".streamlit_tmp")
#     tmp_dir.mkdir(exist_ok=True)
#     dump_path = tmp_dir / "dump.txt"
#     dump_path.write_text(dump_text or "", encoding="utf-8")

#     if not dump_path.read_text().strip():
#         st.error("Dump input is empty. Please paste a dump.")
#     else:
#         # --- Robust Logging Implementation ---
#         st.session_state.log_messages = []
        
#         # Define the logger function that appends to the session state list.
#         def ui_log(m):
#             st.session_state.log_messages.append(m)

#         result = None
        
#         # Display logs in an expander
#         with log_container:
#             with st.expander("🪵 Run Logs", expanded=True):
#                 # Use st.info for a visually distinct log area
#                 log_placeholder = st.empty()
#                 log_placeholder.info("Starting project generation...")

#                 try:
#                     dest_root = resolve_root_dir(base_dir, root_name)
                    
#                     with st.spinner("Parsing and generating project..."):
#                         result = codefill_run(
#                             dump_file=dump_path,
#                             root_dir=dest_root,
#                             mode=mode,
#                             logger=ui_log,
#                         )
#                         # Update logs one last time after the run
#                         log_placeholder.text_area(
#                             "Logs", 
#                             "\n".join(st.session_state.log_messages), 
#                             height=300, 
#                             key="final_log_display", 
#                             label_visibility="collapsed"
#                         )

#                 except Exception as e:
#                     ui_log(f"FATAL ERROR: {e}")
#                     log_placeholder.text_area(
#                         "Logs", 
#                         "\n".join(st.session_state.log_messages), 
#                         height=300, 
#                         key="final_log_display_error", 
#                         label_visibility="collapsed"
#                     )
#                     st.error(f"An unexpected error occurred during the run: {e}")

#         # Display the final results if the run was successful.
#         if result:
#             st.success(f"Project generation complete for `{dest_root}`")
#             st.json(result.get("count", {}))

#             if st.button("🖥️ Open project in editor", key="open_project_after_run", type="secondary"):
#                 st.toast(launch_editor(dest_root))

#             st.subheader(f"🗂️ Generated Files in `{dest_root}`")
#             for p in sorted(_find_all_files(dest_root)):
#                 rel = p.relative_to(dest_root).as_posix()
#                 with st.expander(rel):
#                     try:
#                         content = p.read_text(encoding="utf-8", errors="replace")
#                         lang = guess_language(p)
#                         st.code(content, language=lang or "text")
#                     except Exception as e:
#                         st.error(f"Could not display file: {e}")


# # # apps/streamlit_app.py
# # from __future__ import annotations

# # import os
# # import sys
# # import shlex
# # import shutil
# # import subprocess
# # import hashlib
# # from pathlib import Path

# # import streamlit as st

# # from tools.codefill import codefill_run, resolve_root_dir, _find_all_files  # type: ignore

# # st.set_page_config(page_title="Project Structure Builder", layout="wide")
# # st.title("📂 Project Structure Builder + LLM Code Filler")

# # # ---------------- Sidebar ----------------
# # with st.sidebar:
# #     st.subheader("⚙️ Options")
# #     base_dir = st.text_input("Destination folder (base)", value=os.getenv("CODEFILL_FORCE_BASE_DIR", "/Users/pranavsaji/Downloads/Projects"))
# #     root_name = st.text_input("Root project folder", value=os.getenv("CODEFILL_FORCE_ROOT_NAME", "calendar-hub"))
# #     mode = st.radio("If file exists", options=["overwrite", "skip"], index=0, horizontal=True)
# #     provider = st.selectbox("LLM provider", options=["groq", "openai"], index=0)

# #     st.subheader("LLM reliability")
# #     min_delay_ms = st.number_input("LLM throttle per request (ms)", min_value=0, value=int(os.getenv("LLM_MIN_DELAY_MS", "1200")))
# #     max_retries = st.number_input("LLM max retries (on 429/5xx)", min_value=0, value=int(os.getenv("LLM_MAX_RETRIES", "4")))
# #     backoff_base = st.number_input("LLM backoff base (ms)", min_value=0, value=int(os.getenv("LLM_BACKOFF_BASE_MS", "800")))
# #     backoff_max  = st.number_input("LLM backoff max (ms)", min_value=0, value=int(os.getenv("LLM_BACKOFF_MAX_MS", "8000")))
# #     fallback_to_openai = st.checkbox("Fallback to OpenAI if Groq fails", value=True)
# #     use_bundle = st.checkbox("Use bundle extractor (may trigger 429 on chunks)", value=False)
# #     max_tokens_cap = st.number_input("Cap LLM max_tokens", min_value=512, value=int(os.getenv("LLM_MAX_TOKENS", "2048")))

# #     st.caption(
# #         f"OPENAI_API_KEY set: {'✅' if os.getenv('OPENAI_API_KEY') else '❌'}  |  "
# #         f"GROQ_API_KEY set: {'✅' if os.getenv('GROQ_API_KEY') else '❌'}"
# #     )

# # def _dest_preview(base_dir: str, root_name: str) -> Path:
# #     try:
# #         return (Path(base_dir).expanduser() / root_name).resolve()
# #     except Exception:
# #         return Path(base_dir).expanduser().resolve()

# # def _ensure_dump_file(tmp_dir: Path, dump_text: str, dump_file, save_as_name: str) -> Path:
# #     tmp_dir.mkdir(parents=True, exist_ok=True)
# #     p = tmp_dir / (save_as_name or "dump.txt")
# #     if dump_file is not None:
# #         p.write_bytes(dump_file.getvalue())
# #     else:
# #         p.write_text(dump_text or "", encoding="utf-8")
# #     return p

# # def _lang_for(rel: str) -> str:
# #     r = rel.lower()
# #     if r.endswith(".py"): return "python"
# #     if r.endswith(".md"): return "markdown"
# #     if r.endswith(".yml") or r.endswith(".yaml"): return "yaml"
# #     if r.endswith(".json"): return "json"
# #     if r.endswith(".xml"): return "xml"
# #     if r.endswith(".csv"): return "csv"
# #     if r.endswith("dockerfile"): return "dockerfile"
# #     if r.endswith(".toml"): return "toml"
# #     if r.endswith(".ini"): return "ini"
# #     if r.endswith(".env") or r.endswith(".example"): return ""
# #     return ""

# # def _button_key(prefix: str, path_like: str) -> str:
# #     digest = hashlib.md5(path_like.encode("utf-8", errors="ignore")).hexdigest()[:8]
# #     return f"{prefix}_{digest}"

# # def _launch_editor(target: Path, editor_preference: str | None = None) -> str:
# #     try:
# #         target = target.resolve()
# #     except Exception:
# #         pass
# #     if not target.exists():
# #         return f"Path does not exist: {target}"

# #     attempted: list[list[str]] = []
# #     def _popen(cmd: list[str]) -> bool:
# #         attempted.append(cmd)
# #         try:
# #             subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
# #             return True
# #         except Exception as e:
# #             st.warning(f"Launch error with {cmd}: {e}")
# #             return False

# #     editor_cmd = os.getenv("EDITOR_CMD") or editor_preference
# #     if editor_cmd:
# #         import shlex as _shlex
# #         cmd = _shlex.split(editor_cmd) + [str(target)]
# #         if _popen(cmd): return f"Launched via EDITOR_CMD: `{editor_cmd} {target}`"

# #     if shutil.which("code"):
# #         if _popen(["code", str(target)]): return f"Opened in VS Code: code {target}"
# #     if shutil.which("subl"):
# #         if _popen(["subl", str(target)]): return f"Opened in Sublime Text: subl {target}"
# #     if shutil.which("atom"):
# #         if _popen(["atom", str(target)]): return f"Opened in Atom: atom {target}"

# #     if sys.platform == "darwin" and shutil.which("open"):
# #         if _popen(["open", "-a", "Visual Studio Code", str(target)]):
# #             return f'Opened in "Visual Studio Code": open -a "Visual Studio Code" {target}'
# #         if _popen(["open", str(target)]):
# #             return f"Opened with default app: open {target}"

# #     if os.name == "nt":
# #         try:
# #             attempted.append(["cmd", "/c", "start", "", str(target)])
# #             subprocess.Popen(["cmd", "/c", "start", "", str(target)], shell=True)
# #             return f"Opened with default app: start {target}"
# #         except Exception as e:
# #             return f"Failed to open on Windows: {e}"

# #     if shutil.which("xdg-open"):
# #         if _popen(["xdg-open", str(target)]): return f"Opened with default app: xdg-open {target}"

# #     return (
# #         "No suitable editor launcher found. "
# #         "Install VS Code and ensure `code` is on PATH, or set EDITOR_CMD "
# #         "(e.g. 'code -g').\n\nTried:\n- " + "\n- ".join(" ".join(c) for c in attempted)
# #     )

# # st.markdown("### Paste or upload your **dump**")
# # left, right = st.columns([0.6, 0.4], gap="large")
# # with left:
# #     dump_text = st.text_area("Dump text", height=300, placeholder="Paste the dump here…")
# # with right:
# #     dump_file = st.file_uploader("…or upload dump.txt / dump.md", type=["txt", "md"])
# # save_as_name = st.text_input("Save dump as (filename)", value=os.getenv("DUMP_FILENAME", "dump.txt"))

# # dest_preview = _dest_preview(base_dir, root_name)
# # col_open_now, col_run = st.columns([0.35, 0.65])
# # if col_open_now.button("🖥️ Open current destination in editor", key="open_dest_preview"):
# #     st.toast(_launch_editor(dest_preview))

# # run = col_run.button("Run code fill", type="primary", key="run_codefill")
# # results_container = st.container()
# # st.divider()

# # if run:
# #     try:
# #         # Runner options via env
# #         os.environ["CODEFILL_FORCE_BASE_DIR"] = base_dir
# #         os.environ["CODEFILL_FORCE_ROOT_NAME"] = root_name
# #         os.environ["CODEFILL_MODE"] = mode
# #         os.environ["LLM_PROVIDER"] = provider

# #         # Reliability knobs
# #         os.environ["LLM_MIN_DELAY_MS"] = str(int(min_delay_ms))
# #         os.environ["LLM_MAX_RETRIES"] = str(int(max_retries))
# #         os.environ["LLM_BACKOFF_BASE_MS"] = str(int(backoff_base))
# #         os.environ["LLM_BACKOFF_MAX_MS"] = str(int(backoff_max))
# #         os.environ["LLM_FALLBACK_TO_OPENAI"] = "1" if fallback_to_openai else "0"
# #         os.environ["LLM_MAX_TOKENS"] = str(int(max_tokens_cap))
# #         os.environ["LLM_USE_BUNDLE"] = "1" if use_bundle else "0"

# #         dest_root = resolve_root_dir(base_dir, root_name).resolve()
# #         dump_path = _ensure_dump_file(Path(".streamlit_tmp"), dump_text, dump_file, save_as_name)

# #         # Copy dump into destination (for reproducibility)
# #         try:
# #             dest_root.mkdir(parents=True, exist_ok=True)
# #             (dest_root / save_as_name).write_bytes(dump_path.read_bytes())
# #         except Exception as e:
# #             st.warning(f"Could not copy dump into destination project: {e}")

# #         log_box = st.expander("🪵 Debug logs (normalizer + codefill)", expanded=True)
# #         ui_log = lambda m: log_box.write(m)

# #         with st.spinner("Step 1 — Preparing input"):
# #             ui_log(f"[step] read_dump: preparing - {dump_path}")

# #         with st.spinner("Step 2 — Running LLM builder"):
# #             result = codefill_run(
# #                 dump_file=dump_path,
# #                 root_dir=dest_root,
# #                 mode=mode,
# #                 create_missing=True,
# #                 logger=ui_log,
# #             )

# #         with st.spinner("Step 3 — Rendering results"):
# #             with results_container:
# #                 st.success(f"Code fill completed for {dest_root}")
# #                 st.json(result)

# #         if st.button("🖥️ Open project in editor", key="open_project_after_run", type="secondary"):
# #             st.toast(_launch_editor(dest_root))

# #         st.subheader(f"🗂 Files in {dest_root}")
# #         for p in _find_all_files(dest_root):
# #             rel = p.relative_to(dest_root).as_posix()
# #             key_suffix = _button_key("openfile", rel)
# #             with st.expander(rel, expanded=False):
# #                 topc, codec = st.columns([1.2, 8])
# #                 if topc.button("Open in editor", key=f"btn_{key_suffix}"):
# #                     st.toast(_launch_editor(p))
# #                 try:
# #                     content = p.read_text(encoding="utf-8", errors="replace")
# #                 except Exception as e:
# #                     content = f"<<unable to read>>: {e}"
# #                 codec.code(content, language=("" if "." not in rel else rel.split(".")[-1]))
# #     except Exception as e:
# #         st.error(f"Fill failed: {e}")

# # # # apps/streamlit_app.py
# # # from __future__ import annotations

# # # import os
# # # import sys
# # # import shlex
# # # import shutil
# # # import subprocess
# # # import hashlib
# # # from pathlib import Path

# # # import streamlit as st

# # # from tools.codefill import codefill_run, resolve_root_dir, _find_all_files  # type: ignore

# # # st.set_page_config(page_title="Project Structure Builder", layout="wide")
# # # st.title("📂 Project Structure Builder + LLM Code Filler")

# # # # ----------------------------
# # # # Sidebar options
# # # # ----------------------------
# # # with st.sidebar:
# # #     st.subheader("⚙️ Options")
# # #     base_dir = st.text_input(
# # #         "Destination folder (base)",
# # #         value=os.getenv("CODEFILL_FORCE_BASE_DIR", "/Users/pranavsaji/Downloads/Projects"),
# # #         help="Where to create/find the root project folder.",
# # #     )
# # #     root_name = st.text_input(
# # #         "Root project folder",
# # #         value=os.getenv("CODEFILL_FORCE_ROOT_NAME", ""),
# # #         help="Project folder under the base directory.",
# # #     )
# # #     mode = st.radio("If file exists", options=["overwrite", "skip"], index=0, horizontal=True)
# # #     provider = st.selectbox("LLM provider", options=["groq", "openai"], index=0)
# # #     st.caption(
# # #         f"OPENAI_API_KEY set: {'✅' if os.getenv('OPENAI_API_KEY') else '❌'}  |  "
# # #         f"GROQ_API_KEY set: {'✅' if os.getenv('GROQ_API_KEY') else '❌'}"
# # #     )

# # # # Compute a preview of the intended destination folder (even before running)
# # # def _dest_preview() -> Path:
# # #     try:
# # #         # resolve_root_dir is authoritative after run; for preview use join+resolve
# # #         return (Path(base_dir).expanduser() / root_name).resolve()
# # #     except Exception:
# # #         return Path(base_dir).expanduser().resolve()

# # # # ----------------------------
# # # # Helpers
# # # # ----------------------------
# # # def _ensure_dump_file(tmp_dir: Path, dump_text: str, dump_file) -> Path:
# # #     """
# # #     Persist the user's dump to a temp file for the filler.
# # #     If a file is uploaded we keep the raw bytes; otherwise we encode the text as UTF-8.
# # #     """
# # #     tmp_dir.mkdir(parents=True, exist_ok=True)
# # #     p = tmp_dir / "dump.txt"
# # #     if dump_file is not None:
# # #         p.write_bytes(dump_file.getvalue())
# # #     else:
# # #         p.write_text(dump_text or "", encoding="utf-8")
# # #     return p

# # # def _lang_for(rel: str) -> str:
# # #     r = rel.lower()
# # #     if r.endswith(".py"): return "python"
# # #     if r.endswith(".md"): return "markdown"
# # #     if r.endswith(".yml") or r.endswith(".yaml"): return "yaml"
# # #     if r.endswith(".json"): return "json"
# # #     if r.endswith(".xml"): return "xml"
# # #     if r.endswith(".csv"): return "csv"
# # #     if r.endswith("dockerfile"): return "dockerfile"
# # #     if r.endswith(".toml"): return "toml"
# # #     if r.endswith(".ini"): return "ini"
# # #     if r.endswith(".env") or r.endswith(".example"): return ""
# # #     return ""

# # # def _button_key(prefix: str, path_like: str) -> str:
# # #     # Stable, safe key from path + prefix
# # #     digest = hashlib.md5(path_like.encode("utf-8", errors="ignore")).hexdigest()[:8]
# # #     return f"{prefix}_{digest}"

# # # def _launch_editor(target: Path, editor_preference: str | None = None) -> str:
# # #     """
# # #     Best-effort launcher for VS Code or any GUI editor.
# # #     - Honors EDITOR_CMD if set (e.g. "code -g").
# # #     - Tries 'code', 'subl', 'atom', macOS 'open -a \"Visual Studio Code\"', Windows 'start', Linux 'xdg-open'.
# # #     Returns a human-readable message.
# # #     """
# # #     try:
# # #         target = target.resolve()
# # #     except Exception:
# # #         pass

# # #     if not target.exists():
# # #         return f"Path does not exist: {target}"

# # #     attempted: list[list[str]] = []

# # #     def _popen(cmd: list[str]) -> bool:
# # #         attempted.append(cmd)
# # #         try:
# # #             subprocess.Popen(cmd)
# # #             return True
# # #         except Exception as e:
# # #             st.warning(f"Launch error with {cmd}: {e}")
# # #             return False

# # #     editor_cmd = os.getenv("EDITOR_CMD") or editor_preference
# # #     if editor_cmd:
# # #         cmd = shlex.split(editor_cmd) + [str(target)]
# # #         if _popen(cmd):
# # #             return f"Launched via EDITOR_CMD: `{editor_cmd} {target}`"

# # #     # VS Code CLI
# # #     if shutil.which("code"):
# # #         if _popen(["code", str(target)]):
# # #             return f"Opened in VS Code: code {target}"

# # #     # Sublime / Atom
# # #     if shutil.which("subl"):
# # #         if _popen(["subl", str(target)]):
# # #             return f"Opened in Sublime Text: subl {target}"
# # #     if shutil.which("atom"):
# # #         if _popen(["atom", str(target)]):
# # #             return f"Opened in Atom: atom {target}"

# # #     # macOS
# # #     if sys.platform == "darwin" and shutil.which("open"):
# # #         if _popen(["open", "-a", "Visual Studio Code", str(target)]):
# # #             return f'Opened in "Visual Studio Code": open -a "Visual Studio Code" {target}'
# # #         if _popen(["open", str(target)]):
# # #             return f"Opened with default app: open {target}"

# # #     # Windows
# # #     if os.name == "nt":
# # #         try:
# # #             attempted.append(["cmd", "/c", "start", "", str(target)])
# # #             subprocess.Popen(["cmd", "/c", "start", "", str(target)], shell=True)
# # #             return f"Opened with default app: start {target}"
# # #         except Exception as e:
# # #             return f"Failed to open on Windows: {e}"

# # #     # Linux
# # #     if shutil.which("xdg-open"):
# # #         if _popen(["xdg-open", str(target)]):
# # #             return f"Opened with default app: xdg-open {target}"

# # #     # Final fallback
# # #     return (
# # #         "No suitable editor launcher found. "
# # #         "Install VS Code and ensure `code` is on PATH, or set EDITOR_CMD "
# # #         "(e.g. 'code -g').\n\nTried:\n- " + "\n- ".join(" ".join(c) for c in attempted)
# # #     )

# # # # ----------------------------
# # # # Main UI
# # # # ----------------------------
# # # st.markdown("### Paste or upload your **dump**")

# # # left, right = st.columns([0.6, 0.4], gap="large")
# # # with left:
# # #     dump_text = st.text_area("Dump text", height=300, placeholder="Paste the dump here…")
# # # with right:
# # #     dump_file = st.file_uploader("…or upload dump.txt / dump.md", type=["txt", "md"])

# # # # Always allow opening the target folder (even before run)
# # # dest_preview = _dest_preview()
# # # col_open_now, col_run = st.columns([0.35, 0.65])
# # # if col_open_now.button("🖥️ Open current destination in editor", key="open_dest_preview"):
# # #     msg = _launch_editor(dest_preview)
# # #     st.toast(msg)

# # # run = col_run.button("Run code fill", type="primary", key="run_codefill")
# # # results_container = st.container()
# # # st.divider()

# # # if run:
# # #     try:
# # #         # feed options to filler
# # #         os.environ["CODEFILL_FORCE_BASE_DIR"] = base_dir
# # #         os.environ["CODEFILL_FORCE_ROOT_NAME"] = root_name
# # #         os.environ["CODEFILL_MODE"] = mode
# # #         os.environ["LLM_PROVIDER"] = provider

# # #         # Authoritative destination from helper, ensure absolute
# # #         dest_root = resolve_root_dir(base_dir, root_name).resolve()
# # #         dump_path = _ensure_dump_file(Path(".streamlit_tmp"), dump_text, dump_file)

# # #         # Keep a copy of the dump in the destination for later runs (optional)
# # #         try:
# # #             dest_root.mkdir(parents=True, exist_ok=True)
# # #             (dest_root / "dump.txt").write_bytes(dump_path.read_bytes())
# # #         except Exception as e:
# # #             st.warning(f"Could not copy dump into destination project: {e}")

# # #         # ---- NEW: visible log sink for the normalizer/runner ----
# # #         log_box = st.expander("🪵 Debug logs (normalizer + codefill)", expanded=True)
# # #         ui_log = lambda m: log_box.write(m)

# # #         # run the builder with logging
# # #         result = codefill_run(
# # #             dump_file=dump_path,
# # #             root_dir=dest_root,
# # #             mode=mode,
# # #             create_missing=True,
# # #             logger=ui_log,  # <-- all [normalizer] lines show up here
# # #         )

# # #         with results_container:
# # #             st.success(f"Code fill completed for {dest_root}")
# # #             if result.get("warnings"):
# # #                 with st.expander("Warnings", expanded=False):
# # #                     for w in result["warnings"]:
# # #                         st.write(f"• {w}")
# # #             st.json(result)

# # #         # Quick open project button (guaranteed absolute path)
# # #         if st.button("🖥️ Open project in editor", key="open_project_after_run", type="secondary"):
# # #             msg = _launch_editor(dest_root)
# # #             st.toast(msg)

# # #         st.subheader(f"🗂 Files in {dest_root}")
# # #         for p in _find_all_files(dest_root):
# # #             rel = p.relative_to(dest_root).as_posix()
# # #             key_suffix = _button_key("openfile", rel)
# # #             with st.expander(rel, expanded=False):
# # #                 topc, codec = st.columns([1.2, 8])
# # #                 if topc.button("Open in editor", key=f"btn_{key_suffix}"):
# # #                     msg = _launch_editor(p)
# # #                     st.toast(msg)
# # #                 try:
# # #                     content = p.read_text(encoding="utf-8", errors="replace")
# # #                 except Exception as e:
# # #                     content = f"<<unable to read>>: {e}"
# # #                 codec.code(content, language=_lang_for(rel))

# # #     except Exception as e:
# # #         st.error(f"Fill failed: {e}")

