# 📂 Project Builder

**Project Builder** is a developer productivity tool that takes unstructured dumps (from LLMs, text notes, or manual diagrams) and **automatically generates clean, enterprise-grade project structures with files, code stubs, and optional AI backfill**.

It ships with a **Streamlit UI** for interactive use and a **FastAPI backend** for programmatic access.

---

## ✨ Features

* 🔄 **Convert dumps → code**  
  Paste or upload a raw dump (e.g., `tree` output, markdown doc) and generate structured code.

* 🧹 **Deterministic parsing first**  
  Regex + heuristics parse project structures **before** falling back to LLMs.

* 🤖 **LLM integration (optional)**  
  OpenAI and Groq LLMs for backfilling missing files or extracting structured JSON.

* 🔄 **Open your code directly in VS code**  
    Click button to open the code in VS code
    
* 🛠️ **Streamlit UI (multi-page)**  
  Paste dumps, configure options, and build projects visually.

* 📦 **New: Code Explorer & Exporter**  
  Scan a folder or uploaded ZIP, preview files, and export the selection as:
  - **Markdown** (combined document)
  - **DOCX** (XML-safe; control chars are sanitized)
  - **ZIP** (original sources)
  Includes strong default excludes, file size limit, and **per-file ✕ removal** without losing your scan.

* 🗑️ **Danger Zone file manager**  
  Move items to `.trash/` (safe) or **delete permanently** from the selected base folder.

* ⚙️ **Automation mode**  
  CLI and `.env` settings to auto-generate projects from `dump.txt`.

* 🧰 **Enterprise-ready**  
  `git init`, formatting with Black, Dockerized deployment, and modular codebase.

---

## 🗂️ Project Structure

```

Project-builder/
│── apps/
│   ├── streamlit\_app.py                   # Streamlit multipage entry
│   └── pages/
│       └── 02\_Code\_Explorer\_and\_Exporter.py  # Folder/ZIP explorer + exporters + Danger Zone
│
│── structure\_builder/                     # Core logic
│   ├── core.py                            # Build logic
│   ├── llm\_normalizer.py                  # Hybrid parser + LLM integration
│   ├── groq\_openai.py                     # LLM provider wrapper
│   ├── sanitize.py                        # Path cleaning & heuristics
│   ├── cli.py                             # CLI entrypoint
│   └── audit.py                           # Optional checks & validation
│
│── tools/
│   ├── doc\_export.py                      # Markdown/DOCX/ZIP exporters (XML-safe DOCX)
│   ├── file\_harvester.py                  # Folder walker with size/exclude filters
│   ├── zip\_utils.py                       # In-memory ZIP parsing
│   ├── trashcan.py                        # Move to .trash / permanent delete
│   ├── codefill.py                        # Auto-code filler
│   └── prestart\_codefill.py               # Prestart hook
│
│── .env.example
│── requirements.txt
│── pyproject.toml
│── Dockerfile
│── docker-compose.yml
│── README.md
└── .gitignore

````

---

## ⚡ Quick Start

### 1) Clone and setup

```bash
git clone git@github.com:pranavsaji/Project-builder.git
cd Project-builder

# Setup venv
python3.11 -m venv .venv
source .venv/bin/activate

# Install deps
pip install -r requirements.txt
````

> **Optional for DOCX export:**
> `pip install python-docx`

### 2) Configure environment

Copy `.env.example` → `.env` and set your keys:

```env
LLM_PROVIDER=groq
OPENAI_API_KEY=sk-xxxx
GROQ_API_KEY=gsk-xxxx
```

### 3) Run Streamlit UI

```bash
streamlit run apps/streamlit_app.py
```

Open: [http://localhost:8502](http://localhost:8502)

### 4) Run CLI

```bash
python -m structure_builder.cli --dump ./dump.txt --dest ./output --mode overwrite
```

---

## 🖼️ Streamlit UI

### Build from dump

* **Paste LLM dump** → preview structure.
* Configure: destination folder, overwrite/skip, `git init`, Black formatting, optional LLM backfill.
* Click **Build** → generates the project. A log window shows progress.

### **New: 📦 Code Explorer & Exporter**

Open the **“Code Explorer & Exporter”** page in the sidebar.

**Folder mode**

1. Enter a **Base folder path** and click **Scan folder**.
2. Use **Filters** (sidebar):

   * Max file size (KB)
   * Default excludes (e.g., `.git`, `venv`, `node_modules`, `__pycache__`, …)
   * Include hidden files
3. After scanning, your results persist. Use:

   * **✕** next to any item to remove just that file from the selection (no re-scan needed).
   * **Reset selection** to re-select everything.
   * **Rescan** if you change filters or base path.
4. Export the current selection as **Markdown**, **DOCX**, or **ZIP**.

**ZIP mode**

1. Upload a `.zip` archive.
2. Select files to include.
3. Export as **Markdown**, **DOCX**, or **ZIP**.

**Danger Zone**

* Operates under the chosen base folder:

  * **Move to `.trash/` (safe)** or **Delete permanently**.
  * Type `YES` to confirm.
* Useful for cleaning large/unwanted directories quickly.

---

## 🔌 LLM Providers

* **OpenAI** (`gpt-4o-mini`, `gpt-4o`)
* **Groq** (`gpt-oss-120b`)

Configure via `.env`:

```env
LLM_PROVIDER=groq
OPENAI_API_KEY=sk-...
GROQ_API_KEY=gsk-...
```

---

## 🐳 Docker Support

### Build image

```bash
docker build -t project-builder .
```

### Run with docker-compose

```bash
docker-compose up
```

---

## 🧪 Tests

```bash
pytest -v
```

---

## 🛠️ Troubleshooting

### “ValueError: All strings must be XML compatible…”

This came from `python-docx` when control characters or NULL bytes appear in file content (common with mixed/binary files).
**Fixed:** `tools/doc_export.py` now **sanitizes** text before writing to DOCX (XML-safe). Binary/garbled content is either scrubbed or replaced with a short note.

If DOCX download is disabled, install the optional dependency:

```bash
pip install python-docx
```

### Files missing after scan?

* Increase **Max file size (KB)** in the sidebar.
* Remove matching tokens from **Exclusions** (comma-separated).
* Toggle **Include hidden files**.

---

## 🚀 Roadmap

* [ ] Add **multi-dump merging**
* [ ] Built-in **template library** (Flask, FastAPI, Next.js, etc.)
* [ ] GitHub Actions workflow for lint + test + build
* [ ] WebSocket API for real-time updates
* [ ] Export to ZIP for easy sharing

---

## 🤝 Contributing

Pull requests welcome!
If you’re adding a new feature (e.g., template, provider), please open an issue first.

