# apps/streamlit_app.py
import os
from pathlib import Path
import streamlit as st

from tools.codefill import codefill_run, resolve_root_dir, _find_all_files  # type: ignore

st.set_page_config(page_title="Project Structure Builder", layout="wide")
st.title("📂 Project Structure Builder + LLM Code Filler")

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
    st.caption(f"OPENAI_API_KEY set: {'✅' if os.getenv('OPENAI_API_KEY') else '❌'}  |  GROQ_API_KEY set: {'✅' if os.getenv('GROQ_API_KEY') else '❌'}")

st.markdown("### Paste or upload your **dump**")

left, right = st.columns([0.6, 0.4], gap="large")
with left:
    dump_text = st.text_area("Dump text", height=300, placeholder="Paste the dump here…")
with right:
    dump_file = st.file_uploader("…or upload dump.txt / dump.md", type=["txt", "md"])

run = st.button("Run code fill", type="primary")
results_container = st.container()
st.divider()

def _ensure_dump_file(tmp_dir: Path) -> Path:
    """
    Persist the user's dump to a temp file for the filler.
    If a file is uploaded we keep the raw bytes; otherwise we encode the text as UTF-8.
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    p = tmp_dir / "dump.txt"
    if dump_file is not None:
        # keep original bytes (may be cp1252 etc.) — the filler is robust
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

if run:
    try:
        # feed options to filler
        os.environ["CODEFILL_FORCE_BASE_DIR"] = base_dir
        os.environ["CODEFILL_FORCE_ROOT_NAME"] = root_name
        os.environ["CODEFILL_MODE"] = mode
        os.environ["LLM_PROVIDER"] = provider

        dest_root = resolve_root_dir(base_dir, root_name)
        dump_path = _ensure_dump_file(Path(".streamlit_tmp"))

        # Keep a copy of the dump in the destination for later runs (optional)
        try:
            (dest_root / "dump.txt").write_bytes(dump_path.read_bytes())
        except Exception as e:
            st.warning(f"Could not copy dump into destination project: {e}")

        result = codefill_run(dump_file=dump_path, root_dir=dest_root, mode=mode, create_missing=True)

        with results_container:
            st.success(f"Code fill completed for {dest_root}")
            st.json(result)

        st.subheader(f"🗂 Files in {dest_root}")
        for p in _find_all_files(dest_root):
            rel = p.relative_to(dest_root).as_posix()
            with st.expander(rel, expanded=False):
                try:
                    # show even if file has odd bytes
                    content = p.read_text(encoding="utf-8", errors="replace")
                except Exception as e:
                    content = f"<<unable to read>>: {e}"
                st.code(content, language=_lang_for(rel))

    except Exception as e:
        st.error(f"Fill failed: {e}")
