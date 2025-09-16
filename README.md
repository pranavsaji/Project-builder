# 📂 Project Builder

**Project Builder** is a developer productivity tool that takes unstructured dumps (from LLMs, text notes, or manual diagrams) and **automatically generates clean, enterprise-grade project structures with files, code stubs, and optional AI backfill**.

It comes with a **Streamlit UI** for interactive use and a **FastAPI backend** for programmatic access.

---

## ✨ Features

* 🔄 **Convert dumps → code**
  Paste or upload a raw dump (e.g., `tree` output, markdown doc) and generate structured code.
* 🧹 **Deterministic parsing first**
  Uses regex + heuristics to parse project structures **before** falling back to LLMs.
* 🤖 **LLM integration (optional)**
  Supports OpenAI and Groq LLMs for backfilling missing files or extracting structured JSON.
* 🛠️ **Streamlit UI**
  Paste dumps, configure options, and build projects visually.
* ⚙️ **Automation mode**
  CLI and `.env` settings to auto-generate projects from `dump.txt`.
* 📦 **Enterprise-ready**
  Git init, formatting with Black, Dockerized deployment, and modular codebase.

---

## 🗂️ Project Structure

```
Project-builder/
│── apps/
│   └── streamlit_app.py       # Streamlit UI
│
│── structure_builder/         # Core logic
│   ├── core.py                # Build logic
│   ├── llm_normalizer.py      # Hybrid parser + LLM integration
│   ├── groq_openai.py         # LLM provider wrapper
│   ├── sanitize.py            # Path cleaning & heuristics
│   ├── cli.py                 # CLI entrypoint
│   └── audit.py               # Optional checks & validation
│
│── tools/
│   ├── codefill.py            # Auto-code filler
│   └── prestart_codefill.py   # Prestart hook
│
│── .env.example               # Example environment file
│── requirements.txt
│── pyproject.toml
│── Dockerfile
│── docker-compose.yml
│── README.md
└── .gitignore
```

---

## ⚡ Quick Start

### 1. Clone and setup

```bash
git clone git@github.com:pranavsaji/Project-builder.git
cd Project-builder

# Setup venv
python3.11 -m venv .venv
source .venv/bin/activate

# Install deps
pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env.example` → `.env` and set your keys:

```env
LLM_PROVIDER=groq
OPENAI_API_KEY=sk-xxxx
GROQ_API_KEY=gsk-xxxx
```

### 3. Run Streamlit UI

```bash
streamlit run apps/streamlit_app.py
```

Open: [http://localhost:8502](http://localhost:8502)

### 4. Run CLI

```bash
python -m structure_builder.cli --dump ./dump.txt --dest ./output --mode overwrite
```

---

## 🖼️ Streamlit UI

* **Paste LLM dump** → preview structure
* Configure: destination folder, overwrite/skip, `git init`, Black formatting, LLM backfill
* Click **Build** → generates full project in chosen directory
* Built-in log window shows progress

---

## 🔌 LLM Providers

* **OpenAI** (`gpt-4o-mini`, `gpt-4o`)
* **Groq** (`gpt-oss-120b`)

You can configure via `.env`:

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

## 🚀 Roadmap

* [ ] Add **multi-dump merging**
* [ ] Built-in **template library** (Flask, FastAPI, Next.js, etc.)
* [ ] GitHub Actions workflow for lint + test + build
* [ ] WebSocket API for real-time updates
* [ ] Export to ZIP for easy sharing

---

## 🤝 Contributing

Pull requests welcome!
If you’re adding a new feature (e.g., template, provider), open an issue first.

