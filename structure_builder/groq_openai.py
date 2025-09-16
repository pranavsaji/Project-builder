from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

import requests


def _provider_from_env(default: str) -> str:
    p = (os.getenv("LLM_PROVIDER") or default or "groq").strip().lower()
    return "openai" if p.startswith("openai") else "groq"

def _get_models() -> Tuple[str, str]:
    groq_model = os.getenv("LLM_MODEL_GROQ", "llama-3.1-70b-versatile")
    openai_model = os.getenv("LLM_MODEL_OPENAI", "gpt-4o-mini")
    return groq_model, openai_model

def _have_keys(provider: str) -> bool:
    if provider == "openai":
        return bool(os.getenv("OPENAI_API_KEY"))
    return bool(os.getenv("GROQ_API_KEY"))

def _fallback_structure() -> Dict:
    return {"root": None, "files": []}

def _fallback_backfill(path: str) -> str:
    lower = path.lower()
    if lower.endswith(("requirements.txt",)):
        return (
            "fastapi\nuvicorn[standard]\npydantic\npydantic-settings\nSQLAlchemy\n"
            "requests\nloguru\npython-dotenv\nkafka-python\nparamiko\n"
        )
    if lower.endswith(("dockerfile",)):
        return "FROM python:3.11-slim\nWORKDIR /app\nCOPY . .\n"
    if lower.endswith((".md", "readme")):
        return f"# {path}\n\n_TODO: add documentation._\n"
    if lower.endswith((".py",)):
        return f'"""Auto-backfilled stub for {path}."""\n\n'
    if lower.endswith((".yaml", ".yml")):
        return "# TODO\n"
    return ""


def _chat(provider: str, messages: List[Dict], *, json_mode: bool = False, max_tokens: int = 4096) -> str:
    provider = _provider_from_env(provider)
    groq_model, openai_model = _get_models()

    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "")
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload: Dict = {
            "model": openai_model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]

    api_key = os.getenv("GROQ_API_KEY", "")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": groq_model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]


def _extract_json_object(s: str) -> Optional[Dict]:
    try:
        return json.loads(s)
    except Exception:
        pass
    start = s.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = s[start : i + 1]
                    try:
                        return json.loads(candidate)
                    except Exception:
                        break
        start = s.find("{", start + 1)
    return None


# -------- bulk extraction (unchanged) -------- #

def llm_extract_files(raw_text: str, provider: str = "groq", *, root_hint: Optional[str] = None) -> Dict:
    provider = _provider_from_env(provider)
    if not _have_keys(provider):
        return _fallback_structure()

    sys = (
        "You are a code structure extractor. Given a developer dump containing a project layout and many "
        "```fenced``` code blocks, return ONLY a JSON object of the form:\n"
        '{ "root": string|null, "files": [ { "path": string, "content": string }, ... ] }\n\n'
        "- Keep file contents verbatim from the code fences.\n"
        "- Include files that appear under headings like ## `path/to/file.py` or ### path/to/file.py followed by a code fence.\n"
        "- If a file appears multiple times, prefer the latest complete fence.\n"
        "- Paths should be relative to the project root; do NOT prefix with './' or absolute paths.\n"
        "- Use `root` if you can infer it (e.g., the top folder name). Otherwise null.\n"
        "- Do NOT add commentary or extra keys."
    )
    usr = f"Root hint: {root_hint or '(none)'}\n\nRAW DUMP START\n{raw_text}\nRAW DUMP END"

    try:
        content = _chat(
            provider,
            [{"role": "system", "content": sys}, {"role": "user", "content": usr}],
            json_mode=True,
            max_tokens=8000,
        )
        obj = _extract_json_object(content)
        if not obj or "files" not in obj or not isinstance(obj.get("files"), list):
            return _fallback_structure()
        for it in obj["files"]:
            if "path" not in it or "content" not in it:
                it.clear()
        obj["files"] = [it for it in obj["files"] if it.get("path") and it.get("content") is not None]
        return obj
    except Exception:
        return _fallback_structure()


# -------- single-file, path-focused extraction (NEW) -------- #

def llm_extract_single_file(raw_text: str, rel_path: str, provider: str = "groq") -> str:
    """
    Ask the LLM to return ONLY the exact code content for the given path,
    copied verbatim from a matching fence in RAW DUMP. If none exists, return "".
    """
    provider = _provider_from_env(provider)
    if not _have_keys(provider):
        return ""

    sys = (
        "You are a precise file extractor. You will be given a project dump and a relative path.\n"
        "If the dump contains a code fence that corresponds to that exact file path under a heading "
        "like '## path' or '## `path`', return ONLY the code inside that fence, verbatim. "
        "If multiple, return the most complete/latest version. If not found, return an empty string. "
        "Do NOT return JSON. Do NOT add comments. Return code only, no fences."
    )
    usr = f"PATH: {rel_path}\n\nRAW DUMP START\n{raw_text}\nRAW DUMP END"

    try:
        body = _chat(
            provider,
            [{"role": "system", "content": sys}, {"role": "user", "content": usr}],
            json_mode=False,
            max_tokens=6000,
        )
        # Strip accidental fences if they appear
        s = body.strip()
        if s.startswith("```"):
            s = s.strip("`")
            nl = s.find("\n")
            if nl != -1:
                s = s[nl + 1 :]
        return s
    except Exception:
        return ""


def llm_backfill_file(path: str, hint: str = "", provider: str = "groq", *, context: Optional[str] = None) -> str:
    provider = _provider_from_env(provider)
    if not _have_keys(provider):
        return _fallback_backfill(path)

    ext = path.lower().split(".")[-1] if "." in path else ""
    style = {
        "py": "Production-ready Python for FastAPI/SQLAlchemy service; minimal but working; add short docstrings.",
        "md": "Concise documentation with a clear title and usage examples.",
        "yml": "Minimal, valid YAML.",
        "yaml": "Minimal, valid YAML.",
        "dockerfile": "Minimal Dockerfile.",
    }.get(ext, "Appropriate content for the file.")

    sys = (
        "You are a senior engineer filling in a missing file for a Python FastAPI microservice. "
        "Return ONLY the file body — no code fences."
    )
    usr = (
        f"Path: {path}\nHint: {hint or '(none)'}\nStyle: {style}\n"
        "If Python, ensure imports are consistent and the file imports correctly.\n\n"
        f"Project context (may include other files/spec):\n{context or '(no context)'}\n"
        "Return only the file contents."
    )

    try:
        body = _chat(
            provider,
            [{"role": "system", "content": sys}, {"role": "user", "content": usr}],
            json_mode=False,
            max_tokens=4000,
        )
        if body.strip().startswith("```"):
            body = body.strip().strip("`")
            first_nl = body.find("\n")
            if first_nl != -1:
                body = body[first_nl + 1 :]
        return body.strip() or _fallback_backfill(path)
    except Exception:
        return _fallback_backfill(path)
