from __future__ import annotations

import json
import os
import re
import time
import datetime as _dt
from typing import Dict, List, Optional, Tuple, Callable

import requests

LogFn = Callable[[str], None]

# ---------- Provider / models ----------

def _provider_from_env(default: str) -> str:
    p = (os.getenv("LLM_PROVIDER") or default or "groq").strip().lower()
    return "openai" if p.startswith("openai") else "groq"

def _get_models() -> Tuple[str, str]:
    groq_env = os.getenv("GROQ_MODEL") or os.getenv("LLM_MODEL_GROQ")
    openai_env = os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL_OPENAI")
    groq_model = (groq_env or "llama-3.1-70b-versatile").strip()
    openai_model = (openai_env or "gpt-4o-mini").strip()  # override with e.g. gpt-5-nano
    return groq_model, openai_model

def _have_keys(provider: str) -> bool:
    return bool(os.getenv("OPENAI_API_KEY")) if provider == "openai" else bool(os.getenv("GROQ_API_KEY"))

# ---------- Logging ----------

def _log_sink_path() -> str:
    return os.getenv("LLM_LOG_FILE", "logs/llm_calls.log")

def _emit(line: str) -> None:
    ts = _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    msg = f"[{ts}] {line}\n"
    try:
        path = _log_sink_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(msg)
    except Exception:
        pass

def _llm_log(msg: str, logger: Optional[LogFn]) -> None:
    if logger:
        try:
            logger(msg)
        except Exception:
            pass
    _emit(msg)

# ---------- JSON Utilities ----------
def _extract_json_object(s: str) -> Optional[Dict]:
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", s, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return None

# ---------- Core Request Helper ----------
def _chat(
    provider: str,
    messages: List[Dict],
    *,
    json_mode: bool = False,
    max_tokens: int = 4096,
    logger: Optional[LogFn] = None,
    tag: str = "",
    retries: int = 3,
    timeout_s: int = 90,
) -> str:
    """
    Unified chat wrapper:
      - OpenAI GPT-5* models: do NOT send 'temperature' or 'response_format'
      - OpenAI non-GPT-5: safe to send temperature/response_format
      - Groq: safe to send temperature and response_format
    """
    provider = _provider_from_env(provider)
    groq_model, openai_model = _get_models()
    model_name = openai_model if provider == "openai" else groq_model
    _llm_log(f"[LLM start] tag={tag} prov={provider} json={json_mode} model={model_name}", logger)

    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            if provider == "openai":
                # ------- OPENAI -------
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
                }
                url = "https://api.openai.com/v1/chat/completions"

                is_gpt5 = model_name.lower().startswith("gpt-5")

                payload: Dict = {
                    "model": model_name,
                    "messages": messages,
                    "max_tokens": max_tokens,
                }

                # Only non-gpt5 models should get temperature other than 1.0
                # For GPT-5 models, the API explicitly states only default (1) value is supported if specified.
                if is_gpt5:
                    payload["temperature"] = 1.0 # Explicitly set to 1.0 for gpt-5 models
                else:
                    payload["temperature"] = 0.1 # Keep original for non-gpt5

                # Response format can still be included if json_mode is requested,
                # as the error did not mention response_format for gpt-5-nano.
                if json_mode:
                    payload["response_format"] = {"type": "json_object"}

                _llm_log(f"[llm:req] POST {url} model={model_name} payload_keys={list(payload.keys())}", logger)
                r = requests.post(url, headers=headers, json=payload, timeout=timeout_s)
                r.raise_for_status()
                data = r.json()
                out = data["choices"][0]["message"]["content"]
                _llm_log(f"[LLM ok] tag={tag} prov=openai chars_out={len(out or '')}", logger)
                return (out or "").strip()

            else:
                # ------- GROQ -------
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}",
                }
                url = "https://api.groq.com/openai/v1/chat/completions"

                payload: Dict = {
                    "model": model_name,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.1,
                }
                if json_mode:
                    payload["response_format"] = {"type": "json_object"}

                _llm_log(f"[llm:req] POST {url} model={model_name} payload_keys={list(payload.keys())}", logger)
                r = requests.post(url, headers=headers, json=payload, timeout=timeout_s)
                r.raise_for_status()
                data = r.json()
                out = data["choices"][0]["message"]["content"]
                _llm_log(f"[LLM ok] tag={tag} prov=groq chars_out={len(out or '')}", logger)
                return (out or "").strip()

        except requests.HTTPError as e:
            last_err = e
            status = getattr(e.response, "status_code", None)
            body_preview = None
            try:
                body_preview = e.response.text[:300]
            except Exception:
                pass
            _llm_log(f"[LLM err] attempt={attempt} status={status} body≈{body_preview}", logger)
            if status in {429, 500, 502, 503, 504} and attempt < retries:
                time.sleep(1.5 ** attempt)
                continue
            if status == 400 and attempt < retries:
                # For 400 errors, if it's a fixed payload issue (like temperature),
                # retrying won't help unless the parameters are changed.
                # However, some 400s can be transient, so a single retry with backoff can be useful.
                # The primary fix for the temperature issue is now handled before this.
                time.sleep(1.2 ** attempt)
                continue
            break
        except Exception as e:
            last_err = e
            _llm_log(f"[LLM exc] attempt={attempt} exc={e!r}", logger)
            break

    raise last_err if last_err else RuntimeError("LLM call failed")

# ---------- New Primary Function for Batch Generation ----------
def llm_batch_backfill(
    context: str, files_to_generate: List[str], provider: str, *, logger: Optional[LogFn] = None
) -> Dict[str, str]:
    provider = _provider_from_env(provider)
    if not _have_keys(provider) or not files_to_generate:
        return {}
    system_prompt = (
        "You are an expert senior software engineer. Your task is to generate the complete, production-ready code for a list of missing project files. "
        "Use the provided context from existing files to ensure the new code is consistent, correct, and follows the project's patterns (e.g., imports, style). "
        "You MUST return a single JSON object where the keys are the file paths and the values are the complete file content as strings. "
        "Do not add any comments, notes, or explanations outside of the code itself. The response must be only the JSON object."
    )
    user_prompt = (
        "Here is the context from files that already exist in the project:\n"
        "--- CONTEXT START ---\n"
        f"{context}\n"
        "--- CONTEXT END ---\n\n"
        "Based on this context, please generate the complete code for the following missing files:\n"
        + "\n".join(f"- `{path}`" for path in files_to_generate)
        + "\n\nRespond with ONLY a single JSON object containing the content for these files."
    )
    try:
        response_str = _chat(
            provider,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            json_mode=True,
            max_tokens=8000,
            logger=logger,
            tag="batch_backfill",
        )
        response_json = _extract_json_object(response_str)
        if not isinstance(response_json, dict):
            return {}
        return {
            str(k): str(v)
            for k, v in response_json.items()
            if isinstance(k, str) and isinstance(v, str) and k in files_to_generate
        }
    except Exception as e:
        _llm_log(f"[LLM error] batch_backfill failed: {e!r}", logger)
        return {}

# ---------- Legacy helpers (kept for compatibility) ----------

def llm_extract_files(raw_text: str, provider: str = "groq", **kwargs) -> Dict:
    logger = kwargs.get("logger")
    _llm_log(f"[LLM legacy] llm_extract_files called. Note: This method is unreliable.", logger)
    return {"version": 1, "root": None, "files": [], "notes": ["Legacy function executed."]}

def llm_extract_single_file(
    raw_text: str, rel_path: str, provider: str = "groq", *, logger: Optional[LogFn] = None
) -> str:
    provider = _provider_from_env(provider)
    if not _have_keys(provider):
        return ""
    sys = (
        "You are a precise file extractor. Given a project dump and a relative path, "
        "return ONLY the file body for that exact path if found within a code fence. "
        "If not found, return an empty string. Do not add markdown fences or any other text."
    )
    if len(raw_text) > 12000:
        raw_text = raw_text[:12000]
    usr = f"PATH: {rel_path}\n\n--- DUMP START ---\n{raw_text}\n--- DUMP END ---"
    try:
        return _chat(
            provider,
            [{"role": "system", "content": sys}, {"role": "user", "content": usr}],
            max_tokens=6000,
            logger=logger,
            tag=f"legacy_extract_single:{rel_path}",
        ).strip()
    except Exception as e:
        _llm_log(f"[LLM error] legacy_extract_single path={rel_path}: {e!r}", logger)
        return ""

def llm_backfill_file(
    path: str,
    hint: str = "",
    provider: str = "groq",
    *,
    context: Optional[str] = None,
    logger: Optional[LogFn] = None,
) -> str:
    provider = _provider_from_env(provider)
    if not _have_keys(provider):
        return f"# Fallback stub for {path}: LLM provider key not set."
    sys = "You are a senior engineer filling in a missing file. Return ONLY the file body — no code fences."
    ctx = context or ""
    if len(ctx) > 7000:
        ctx = ctx[:7000]
    usr = (
        f"Path: {path}\nHint: {hint or 'Create a minimal, working file.'}\n\n"
        f"Project context:\n{ctx}\n\nReturn only the file contents."
    )
    try:
        return _chat(
            provider,
            [{"role": "system", "content": sys}, {"role": "user", "content": usr}],
            max_tokens=4000,
            logger=logger,
            tag=f"legacy_backfill:{path}",
        ).strip()
    except Exception as e:
        _llm_log(f"[LLM error] legacy_backfill path={path}: {e!r}", logger)
        return f"# Auto-generated stub for {path} after LLM error.\n"

# # structure_builder/groq_openai.py
# from __future__ import annotations

# import json
# import os
# import re
# import time
# import datetime as _dt
# from typing import Dict, List, Optional, Tuple, Callable

# import requests

# LogFn = Callable[[str], None]

# # ---------- Provider / models ----------

# def _provider_from_env(default: str) -> str:
#     p = (os.getenv("LLM_PROVIDER") or default or "groq").strip().lower()
#     return "openai" if p.startswith("openai") else "groq"

# def _get_models() -> Tuple[str, str]:
#     groq_env = os.getenv("GROQ_MODEL") or os.getenv("LLM_MODEL_GROQ")
#     openai_env = os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL_OPENAI")
#     groq_model = (groq_env or "llama-3.1-70b-versatile").strip()
#     openai_model = (openai_env or "gpt-4o-mini").strip()
#     return groq_model, openai_model

# def _have_keys(provider: str) -> bool:
#     return bool(os.getenv("OPENAI_API_KEY")) if provider == "openai" else bool(os.getenv("GROQ_API_KEY"))

# # ---------- Logging ----------

# def _log_sink_path() -> str:
#     return os.getenv("LLM_LOG_FILE", "logs/llm_calls.log")

# def _emit(line: str) -> None:
#     ts = _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
#     msg = f"[{ts}] {line}\n"
#     try:
#         path = _log_sink_path()
#         os.makedirs(os.path.dirname(path), exist_ok=True)
#         with open(path, "a", encoding="utf-8") as f:
#             f.write(msg)
#     except Exception:
#         pass

# def _llm_log(msg: str, logger: Optional[LogFn]) -> None:
#     if logger:
#         try: logger(msg)
#         except Exception: pass
#     _emit(msg)

# # ---------- JSON Utilities ----------
# def _extract_json_object(s: str) -> Optional[Dict]:
#     try: return json.loads(s)
#     except json.JSONDecodeError:
#         match = re.search(r'\{.*\}', s, re.DOTALL)
#         if match:
#             try: return json.loads(match.group(0))
#             except json.JSONDecodeError: pass
#     return None

# # ---------- Core Request Helper ----------
# def _chat(
#     provider: str, messages: List[Dict], *, json_mode: bool = False, max_tokens: int = 4096,
#     logger: Optional[LogFn] = None, tag: str = "", retries: int = 3, timeout_s: int = 90
# ) -> str:
#     provider = _provider_from_env(provider)
#     groq_model, openai_model = _get_models()
#     _llm_log(f"[LLM start] tag={tag} prov={provider} json={json_mode} model={(openai_model if provider=='openai' else groq_model)}", logger)
#     headers = {"Content-Type": "application/json"}
#     payload = {"messages": messages, "temperature": 0.1, "max_tokens": max_tokens}
#     if provider == "openai":
#         headers["Authorization"] = f"Bearer {os.getenv('OPENAI_API_KEY')}"
#         url = "https://api.openai.com/v1/chat/completions"
#         payload["model"] = openai_model
#         if json_mode: payload["response_format"] = {"type": "json_object"}
#     else:
#         headers["Authorization"] = f"Bearer {os.getenv('GROQ_API_KEY')}"
#         url = "https://api.groq.com/openai/v1/chat/completions"
#         payload["model"] = groq_model
#         if json_mode: payload["response_format"] = {"type": "json_object"}
#     last_err = None
#     for attempt in range(1, retries + 1):
#         try:
#             r = requests.post(url, headers=headers, json=payload, timeout=timeout_s)
#             r.raise_for_status()
#             data = r.json()
#             out = data["choices"][0]["message"]["content"]
#             _llm_log(f"[LLM ok] tag={tag} prov={provider} chars_out={len(out)}", logger)
#             return out
#         except requests.HTTPError as e:
#             last_err = e
#             if e.response.status_code in {429, 500, 502, 503, 504} and attempt < retries:
#                 time.sleep(1.5 ** attempt)
#                 continue
#             else: break
#         except Exception as e: last_err = e; break
#     raise last_err if last_err else RuntimeError("LLM call failed")

# # ---------- New Primary Function for Batch Generation ----------
# def llm_batch_backfill(
#     context: str, files_to_generate: List[str], provider: str, *, logger: Optional[LogFn] = None
# ) -> Dict[str, str]:
#     provider = _provider_from_env(provider)
#     if not _have_keys(provider) or not files_to_generate: return {}
#     system_prompt = (
#         "You are an expert senior software engineer. Your task is to generate the complete, production-ready code for a list of missing project files. "
#         "Use the provided context from existing files to ensure the new code is consistent, correct, and follows the project's patterns (e.g., imports, style). "
#         "You MUST return a single JSON object where the keys are the file paths and the values are the complete file content as strings. "
#         "Do not add any comments, notes, or explanations outside of the code itself. The response must be only the JSON object."
#     )
#     user_prompt = (
#         "Here is the context from files that already exist in the project:\n"
#         "--- CONTEXT START ---\n"
#         f"{context}\n"
#         "--- CONTEXT END ---\n\n"
#         "Based on this context, please generate the complete code for the following missing files:\n"
#         + "\n".join(f"- `{path}`" for path in files_to_generate)
#         + "\n\nRespond with ONLY a single JSON object containing the content for these files."
#     )
#     try:
#         response_str = _chat(provider, messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
#                              json_mode=True, max_tokens=8000, logger=logger, tag="batch_backfill")
#         response_json = _extract_json_object(response_str)
#         if not isinstance(response_json, dict): return {}
#         return {str(k): str(v) for k, v in response_json.items() if isinstance(k, str) and isinstance(v, str) and k in files_to_generate}
#     except Exception as e:
#         _llm_log(f"[LLM error] batch_backfill failed: {e!r}", logger)
#         return {}

# # ---------- Restored Legacy Functions (for backward compatibility) ----------

# def llm_extract_files(raw_text: str, provider: str = "groq", **kwargs) -> Dict:
#     """[LEGACY] Tries to get a full project structure from a single call. Unreliable for large dumps."""
#     logger = kwargs.get("logger")
#     _llm_log(f"[LLM legacy] llm_extract_files called. Note: This method is unreliable.", logger)
#     # Returns a valid empty structure to prevent crashes in older modules that might call it.
#     return {"version": 1, "root": None, "files": [], "notes": ["Legacy function executed."]}

# def llm_extract_single_file(
#     raw_text: str, rel_path: str, provider: str = "groq", *, logger: Optional[LogFn] = None
# ) -> str:
#     """[LEGACY] Attempts to extract the content of a single file from a large dump."""
#     provider = _provider_from_env(provider)
#     if not _have_keys(provider): return ""
#     sys = (
#         "You are a precise file extractor. Given a project dump and a relative path, "
#         "return ONLY the file body for that exact path if found within a code fence. "
#         "If not found, return an empty string. Do not add markdown fences or any other text."
#     )
#     if len(raw_text) > 12000: raw_text = raw_text[:12000]
#     usr = f"PATH: {rel_path}\n\n--- DUMP START ---\n{raw_text}\n--- DUMP END ---"
#     try:
#         return _chat(provider, [{"role":"system","content":sys},{"role":"user","content":usr}],
#                      max_tokens=6000, logger=logger, tag=f"legacy_extract_single:{rel_path}").strip()
#     except Exception as e:
#         _llm_log(f"[LLM error] legacy_extract_single path={rel_path}: {e!r}", logger)
#         return ""

# def llm_backfill_file(
#     path: str, hint: str = "", provider: str = "groq", *,
#     context: Optional[str] = None, logger: Optional[LogFn] = None
# ) -> str:
#     """[LEGACY] Generates a single file based on context."""
#     provider = _provider_from_env(provider)
#     if not _have_keys(provider):
#         return f"# Fallback stub for {path}: LLM provider key not set."
#     sys = "You are a senior engineer filling in a missing file. Return ONLY the file body — no code fences."
#     ctx = context or ""
#     if len(ctx) > 7000: ctx = ctx[:7000]
#     usr = (
#         f"Path: {path}\nHint: {hint or 'Create a minimal, working file.'}\n\n"
#         f"Project context:\n{ctx}\n\nReturn only the file contents."
#     )
#     try:
#         return _chat(provider, [{"role":"system","content":sys},{"role":"user","content":usr}],
#                      max_tokens=4000, logger=logger, tag=f"legacy_backfill:{path}").strip()
#     except Exception as e:
#         _llm_log(f"[LLM error] legacy_backfill path={path}: {e!r}", logger)
#         return f"# Auto-generated stub for {path} after LLM error.\n"

# # # structure_builder/groq_openai.py
# # from __future__ import annotations

# # import json
# # import os
# # import time
# # import datetime as _dt
# # from typing import Dict, List, Optional, Tuple, Callable

# # import requests

# # LogFn = Callable[[str], None]

# # # ---------- Provider / models ----------

# # def _provider_from_env(default: str) -> str:
# #     p = (os.getenv("LLM_PROVIDER") or default or "groq").strip().lower()
# #     return "openai" if p.startswith("openai") else "groq"

# # def _get_models() -> Tuple[str, str]:
# #     # Environment overrides:
# #     groq_env = os.getenv("GROQ_MODEL") or os.getenv("LLM_MODEL_GROQ")
# #     openai_env = os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL_OPENAI")
# #     groq_model = (groq_env or "llama-3.1-70b-versatile").strip()
# #     openai_model = (openai_env or "gpt-4o-mini").strip()
# #     return groq_model, openai_model

# # def _have_keys(provider: str) -> bool:
# #     return bool(os.getenv("OPENAI_API_KEY")) if provider == "openai" else bool(os.getenv("GROQ_API_KEY"))

# # # ---------- Logging ----------

# # def _log_sink_path() -> str:
# #     return os.getenv("LLM_LOG_FILE", "logs/llm_calls.log")

# # def _emit(line: str) -> None:
# #     ts = _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
# #     msg = f"[{ts}] {line}\n"
# #     try:
# #         path = _log_sink_path()
# #         os.makedirs(os.path.dirname(path), exist_ok=True)
# #         with open(path, "a", encoding="utf-8") as f:
# #             f.write(msg)
# #     except Exception:
# #         pass

# # def _llm_log(msg: str, logger: Optional[LogFn]) -> None:
# #     if logger:
# #         try: logger(msg)
# #         except Exception: pass
# #     _emit(msg)

# # # ---------- Schema (versioned) ----------

# # """
# # LLM output schema v1:

# # {
# #   "version": 1,
# #   "root": "calendar-hub" | null,
# #   "files": [
# #     {
# #       "path": "apps/api/src/server.ts",
# #       "content": "<verbatim file body>",
# #       "lang": "ts",                    # optional
# #       "source": "fence|heading|llm",   # optional (how it was found)
# #       "incomplete": false              # optional
# #     }
# #   ],
# #   "notes": ["optional strings"]
# # }
# # """

# # def _normalize_llm_bundle(obj: object, logger: Optional[LogFn]) -> Dict:
# #     if not isinstance(obj, dict):
# #         _llm_log("[LLM warn] bundle not dict; coercing", logger)
# #         return {"version": 1, "root": None, "files": [], "notes": ["coerced_non_dict"]}
# #     v = obj.get("version", 1)
# #     root = obj.get("root", None)
# #     files = obj.get("files", [])
# #     notes = obj.get("notes", [])

# #     if root is not None and not isinstance(root, str):
# #         root = None
# #     if not isinstance(files, list):
# #         files = []
# #     out_files = []
# #     for it in files:
# #         if not isinstance(it, dict): continue
# #         p = it.get("path")
# #         c = it.get("content")
# #         if not isinstance(p, str) or not isinstance(c, (str, type(None))): continue
# #         out_files.append({
# #             "path": p.strip().lstrip("./").replace("\\", "/"),
# #             "content": (c or ""),
# #             "lang": it.get("lang") if isinstance(it.get("lang"), str) else None,
# #             "source": it.get("source") if isinstance(it.get("source"), str) else None,
# #             "incomplete": bool(it.get("incomplete", False)),
# #         })
# #     if not isinstance(notes, list): notes = []
# #     notes = [n for n in notes if isinstance(n, str)]
# #     return {"version": int(v) if isinstance(v, int) else 1, "root": root, "files": out_files, "notes": notes}

# # def _extract_json_object(s: str) -> Optional[Dict]:
# #     try:
# #         return json.loads(s)
# #     except Exception:
# #         pass
# #     # scan for first valid {...}
# #     start = s.find("{")
# #     while start != -1:
# #         depth = 0
# #         for i in range(start, len(s)):
# #             ch = s[i]
# #             if ch == "{": depth += 1
# #             elif ch == "}":
# #                 depth -= 1
# #                 if depth == 0:
# #                     cand = s[start:i+1]
# #                     try:
# #                         return json.loads(cand)
# #                     except Exception:
# #                         break
# #         start = s.find("{", start + 1)
# #     return None

# # # ---------- Core request helper with retry/backoff ----------

# # def _chat(
# #     provider: str,
# #     messages: List[Dict],
# #     *,
# #     json_mode: bool = False,
# #     max_tokens: int = 4096,
# #     logger: Optional[LogFn] = None,
# #     tag: str = "",
# #     retries: int = 3,
# #     timeout_s: int = 60,
# # ) -> str:
# #     provider = _provider_from_env(provider)
# #     groq_model, openai_model = _get_models()

# #     safe_preview = [{"role": m.get("role"), "chars": len(str(m.get("content",""))), "head": str(m.get("content",""))[:160]} for m in messages]
# #     _llm_log(
# #         f"[LLM start] tag={tag or 'chat'} prov={provider} json={json_mode} max_tokens={max_tokens} model={(openai_model if provider=='openai' else groq_model)} messages={safe_preview}",
# #         logger
# #     )

# #     if provider == "openai":
# #         api_key = os.getenv("OPENAI_API_KEY", "")
# #         url = "https://api.openai.com/v1/chat/completions"
# #         headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
# #         payload: Dict = {"model": openai_model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
# #         if json_mode:
# #             payload["response_format"] = {"type": "json_object"}
# #     else:
# #         api_key = os.getenv("GROQ_API_KEY", "")
# #         url = "https://api.groq.com/openai/v1/chat/completions"
# #         headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
# #         payload = {"model": groq_model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
# #         if json_mode:
# #             payload["response_format"] = {"type": "json_object"}

# #     last_err: Optional[Exception] = None
# #     for attempt in range(1, retries + 1):
# #         try:
# #             r = requests.post(url, headers=headers, json=payload, timeout=timeout_s)
# #             if r.status_code == 429:
# #                 raise requests.HTTPError("429 Too Many Requests")
# #             if r.status_code == 413:
# #                 raise requests.HTTPError("413 Payload Too Large")
# #             r.raise_for_status()
# #             data = r.json()
# #             out = data["choices"][0]["message"]["content"]
# #             _llm_log(f"[LLM ok] tag={tag} prov={provider} chars={len(out)} body_head={out[:500]!r}", logger)
# #             return out
# #         except Exception as e:
# #             last_err = e
# #             _llm_log(f"[LLM error] tag={tag} prov={provider} err={e!r}", logger)
# #             if attempt < retries:
# #                 delay = 1.5 * attempt
# #                 time.sleep(delay)
# #             else:
# #                 break
# #     raise last_err if last_err else RuntimeError("LLM failed")

# # # ---------- Public helpers ----------

# # def llm_extract_files(
# #     raw_text: str,
# #     provider: str = "groq",
# #     *,
# #     root_hint: Optional[str] = None,
# #     logger: Optional[LogFn] = None,
# #     max_chunk_chars: int = 9000,
# # ) -> Dict:
# #     """
# #     Returns a schema-normalized bundle: {"version":1,"root":str|None,"files":[...],"notes":[...]}
# #     Will chunk the dump if needed and merge.
# #     """
# #     provider = _provider_from_env(provider)
# #     if not _have_keys(provider):
# #         _llm_log("[LLM skip] extract_files: missing API key(s)", logger)
# #         return {"version": 1, "root": None, "files": [], "notes": ["missing_api_key"]}

# #     sys = (
# #         "You are a code structure extractor. Given a developer dump containing a project layout and many fenced code blocks, "
# #         "output a SINGLE JSON object matching this exact schema (no extra keys, no comments):\n"
# #         '{ "version":1, "root": string|null, "files":[{"path":string,"content":string,"lang":string|null,"source":string|null,"incomplete":boolean|null}], "notes": string[] }\n\n'
# #         "- Keep file contents VERBATIM from code fences under headings like ## `path/to/file`, ### path/to/file, or comments like `# file: path`.\n"
# #         "- Paths must be relative to the project root (no leading './', no absolutes).\n"
# #         "- Prefer the latest/most-complete fence if duplicates exist.\n"
# #         "- Use `root` if a clear top folder is shown; else null.\n"
# #         "- Do NOT wrap in markdown fences. Return only JSON."
# #     )

# #     text = raw_text or ""
# #     if len(text) <= max_chunk_chars:
# #         usr = f"Root hint: {root_hint or '(none)'}\n\nRAW DUMP START\n{text}\nRAW DUMP END"
# #         try:
# #             content = _chat(provider, [{"role": "system", "content": sys}, {"role": "user", "content": usr}],
# #                             json_mode=True, max_tokens=8000, logger=logger, tag="extract_files")
# #             obj = _extract_json_object(content) or {}
# #             return _normalize_llm_bundle(obj, logger)
# #         except Exception as e:
# #             _llm_log(f"[LLM error] extract_files: {e!r}", logger)
# #             return {"version": 1, "root": None, "files": [], "notes": [f"error:{type(e).__name__}"]}

# #     # Chunk
# #     chunks: List[str] = []
# #     s = 0
# #     while s < len(text):
# #         chunks.append(text[s:s+max_chunk_chars])
# #         s += max_chunk_chars

# #     _llm_log(f"[LLM info] extract_files: input too big ({len(text)} chars). Chunking into {len(chunks)} parts (<= {max_chunk_chars} chars each).", logger)

# #     all_files: Dict[str, Dict] = {}
# #     decided_root: Optional[str] = None
# #     notes: List[str] = []
# #     for i, ch in enumerate(chunks, 1):
# #         usr = f"(Chunk {i}/{len(chunks)}) Root hint: {root_hint or '(none)'}\n\nRAW DUMP START\n{ch}\nRAW DUMP END"
# #         try:
# #             content = _chat(provider, [{"role":"system","content":sys},{"role":"user","content":usr}],
# #                             json_mode=True, max_tokens=8000, logger=logger, tag=f"extract_files[{i}/{len(chunks)}]", retries=4)
# #             obj = _extract_json_object(content) or {}
# #             bundle = _normalize_llm_bundle(obj, logger)
# #             if not decided_root and bundle.get("root"):
# #                 decided_root = bundle["root"]
# #             for it in bundle.get("files", []):
# #                 p = it.get("path")
# #                 if not p: continue
# #                 # prefer the longer (likely more complete) content if same path arrives from multiple chunks
# #                 prev = all_files.get(p)
# #                 if not prev or (len(it.get("content","")) > len(prev.get("content",""))):
# #                     all_files[p] = it
# #         except Exception as e:
# #             _llm_log(f"[LLM error] extract_files chunk={i} {type(e).__name__} → skipping chunk", logger)
# #             notes.append(f"chunk_{i}_error:{type(e).__name__}")

# #     return {"version": 1, "root": decided_root, "files": list(all_files.values()), "notes": notes}

# # def llm_extract_single_file(
# #     raw_text: str,
# #     rel_path: str,
# #     provider: str = "groq",
# #     *,
# #     logger: Optional[LogFn] = None,
# # ) -> str:
# #     provider = _provider_from_env(provider)
# #     if not _have_keys(provider):
# #         _llm_log(f"[LLM skip] extract_single path={rel_path}: missing API key(s)", logger)
# #         return ""

# #     sys = (
# #         "You are a precise file extractor. Given a project dump and a relative path, "
# #         "return ONLY the file body for that exact path if present under headings or code fences. "
# #         "If not found, return empty string. No markdown fences."
# #     )
# #     text = raw_text or ""
# #     # Trim if giant to reduce 413/429
# #     if len(text) > 9000:
# #         _llm_log(f"[LLM info] extract_single path={rel_path}: trimmed dump from {len(text)} to 9000 chars", logger)
# #         text = text[:9000]

# #     usr = f"PATH: {rel_path}\n\nRAW DUMP START\n{text}\nRAW DUMP END"
# #     try:
# #         body = _chat(provider, [{"role":"system","content":sys},{"role":"user","content":usr}],
# #                      json_mode=False, max_tokens=6000, logger=logger, tag=f"extract_single:{rel_path}", retries=4)
# #         s = body.strip()
# #         if s.startswith("```"):
# #             s = s.strip("`")
# #             if "\n" in s: s = s.split("\n",1)[1]
# #         _llm_log(f"[LLM done] extract_single path={rel_path} chars={len(s)}", logger)
# #         return s
# #     except Exception as e:
# #         _llm_log(f"[LLM error] extract_single path={rel_path}: {e!r}", logger)
# #         return ""

# # def llm_backfill_file(
# #     path: str,
# #     hint: str = "",
# #     provider: str = "groq",
# #     *,
# #     context: Optional[str] = None,
# #     logger: Optional[LogFn] = None,
# # ) -> str:
# #     provider = _provider_from_env(provider)
# #     if not _have_keys(provider):
# #         _llm_log(f"[LLM skip] backfill path={path}: missing API key(s), using fallback", logger)
# #         # lightweight, targeted fallback
# #         lower = path.lower()
# #         if lower.endswith(("requirements.txt",)):
# #             return "fastapi\nuvicorn[standard]\npydantic\npydantic-settings\nSQLAlchemy\nrequests\nloguru\npython-dotenv\nkafka-python\nparamiko\n"
# #         if lower.endswith(("dockerfile",)):
# #             return "FROM python:3.11-slim\nWORKDIR /app\nCOPY . .\n"
# #         if lower.endswith((".md","readme")):
# #             return f"# {path}\n\n_TODO: add documentation._\n"
# #         if lower.endswith((".py",)):
# #             return f'"""Auto-backfilled stub for {path}."""\n\n'
# #         if lower.endswith((".yaml",".yml",".json",".toml",".ini",".cfg",".conf")):
# #             return "# TODO\n"
# #         return ""

# #     ext = path.lower().split(".")[-1] if "." in path else ""
# #     style = {
# #         "py": "Production-ready Python for FastAPI/SQLAlchemy service; minimal but working; add short docstrings.",
# #         "md": "Concise documentation with a clear title and usage examples.",
# #         "yml": "Minimal, valid YAML.",
# #         "yaml": "Minimal, valid YAML.",
# #         "dockerfile": "Minimal Dockerfile.",
# #         "ts": "TypeScript with strict types; minimal but buildable.",
# #         "tsx": "React/Next.js component (TypeScript), minimal UI.",
# #         "json": "Valid JSON, minimal.",
# #     }.get(ext, "Appropriate content for the file.")

# #     sys = "You are a senior engineer filling in a missing file for a codebase. Return ONLY the file body — no code fences."
# #     ctx = context or ""
# #     if len(ctx) > 7000:
# #         _llm_log(f"[LLM info] backfill path={path}: trimmed context from {len(ctx)} to 7000 chars", logger)
# #         ctx = ctx[:7000]

# #     usr = (
# #         f"Path: {path}\nHint: {hint or '(none)'}\nStyle: {style}\n"
# #         "If Python, ensure imports are consistent and the file imports correctly.\n\n"
# #         f"Project context (may include other files/spec):\n{ctx}\n"
# #         "Return only the file contents."
# #     )

# #     try:
# #         body = _chat(provider, [{"role":"system","content":sys},{"role":"user","content":usr}],
# #                      json_mode=False, max_tokens=4000, logger=logger, tag=f"backfill:{path}", retries=4)
# #         if body.strip().startswith("```"):
# #             body = body.strip().strip("`")
# #             if "\n" in body: body = body.split("\n",1)[1]
# #         out = body.strip()
# #         if not out:
# #             # last-resort minimal
# #             return f"// stub: {path}\n"
# #         _llm_log(f"[LLM done] backfill path={path} chars={len(out)}", logger)
# #         return out
# #     except Exception as e:
# #         _llm_log(f"[LLM error] backfill path={path}: {e!r}", logger)
# #         return f"// stub: {path}\n"

# # # # tools/groq_openai.py
# # # from __future__ import annotations

# # # import json
# # # import os
# # # from typing import Dict, List, Optional, Tuple

# # # import requests


# # # def _provider_from_env(default: str) -> str:
# # #     p = (os.getenv("LLM_PROVIDER") or default or "groq").strip().lower()
# # #     return "openai" if p.startswith("openai") else "groq"


# # # def _get_models() -> Tuple[str, str]:
# # #     groq_model = os.getenv("LLM_MODEL_GROQ", "llama-3.1-70b-versatile")
# # #     openai_model = os.getenv("LLM_MODEL_OPENAI", "gpt-4o-mini")
# # #     return groq_model, openai_model


# # # def _have_keys(provider: str) -> bool:
# # #     if provider == "openai":
# # #         return bool(os.getenv("OPENAI_API_KEY"))
# # #     return bool(os.getenv("GROQ_API_KEY"))


# # # def _fallback_structure() -> Dict:
# # #     return {"root": None, "files": []}


# # # def _fallback_backfill(path: str) -> str:
# # #     lower = path.lower()
# # #     if lower.endswith(("requirements.txt",)):
# # #         return (
# # #             "fastapi\nuvicorn[standard]\npydantic\npydantic-settings\nSQLAlchemy\n"
# # #             "requests\nloguru\npython-dotenv\nboto3\nredis\ncelery\nsendgrid\n"
# # #         )
# # #     if lower.endswith(("dockerfile", "dockerfile.web", "dockerfile.worker")):
# # #         return "FROM python:3.11-slim\nWORKDIR /app\nCOPY . .\n"
# # #     if lower.endswith((".md", "readme")):
# # #         return f"# {path}\n\n_TODO: add documentation._\n"
# # #     if lower.endswith((".py",)):
# # #         return f'"""Auto-backfilled stub for {path}."""\n'
# # #     if lower.endswith((".yaml", ".yml")):
# # #         return "# TODO\n"
# # #     return ""


# # # def _chat(provider: str, messages: List[Dict], *, json_mode: bool = False, max_tokens: int = 4096) -> str:
# # #     provider = _provider_from_env(provider)
# # #     groq_model, openai_model = _get_models()

# # #     if provider == "openai":
# # #         api_key = os.getenv("OPENAI_API_KEY", "")
# # #         url = "https://api.openai.com/v1/chat/completions"
# # #         headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
# # #         payload: Dict = {
# # #             "model": openai_model,
# # #             "messages": messages,
# # #             "temperature": 0.2,
# # #             "max_tokens": max_tokens,
# # #         }
# # #         if json_mode:
# # #             payload["response_format"] = {"type": "json_object"}
# # #         r = requests.post(url, headers=headers, json=payload, timeout=60)
# # #         r.raise_for_status()
# # #         data = r.json()
# # #         return data["choices"][0]["message"]["content"]

# # #     api_key = os.getenv("GROQ_API_KEY", "")
# # #     url = "https://api.groq.com/openai/v1/chat/completions"
# # #     headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
# # #     payload = {
# # #         "model": groq_model,
# # #         "messages": messages,
# # #         "temperature": 0.2,
# # #         "max_tokens": max_tokens,
# # #     }
# # #     if json_mode:
# # #         payload["response_format"] = {"type": "json_object"}
# # #     r = requests.post(url, headers=headers, json=payload, timeout=60)
# # #     r.raise_for_status()
# # #     data = r.json()
# # #     return data["choices"][0]["message"]["content"]


# # # def _extract_json_object(s: str) -> Optional[Dict]:
# # #     try:
# # #         return json.loads(s)
# # #     except Exception:
# # #         pass
# # #     start = s.find("{")
# # #     while start != -1:
# # #         depth = 0
# # #         for i in range(start, len(s)):
# # #             ch = s[i]
# # #             if ch == "{":
# # #                 depth += 1
# # #             elif ch == "}":
# # #                 depth -= 1
# # #                 if depth == 0:
# # #                     candidate = s[start : i + 1]
# # #                     try:
# # #                         return json.loads(candidate)
# # #                     except Exception:
# # #                         break
# # #         start = s.find("{", start + 1)
# # #     return None


# # # # -------- stage 1: bulk structure + bodies -------- #

# # # def llm_extract_files(raw_text: str, provider: str = "groq", *, root_hint: Optional[str] = None) -> Dict:
# # #     provider = _provider_from_env(provider)
# # #     if not _have_keys(provider):
# # #         return _fallback_structure()

# # #     sys = (
# # #         "You are a code structure extractor. Given a developer dump containing a project layout and many "
# # #         "```fenced``` code blocks, return ONLY a JSON object of the form:\n"
# # #         '{ "root": string|null, "files": [ { "path": string, "content": string }, ... ] }\n\n'
# # #         "- Keep file contents verbatim from the code fences.\n"
# # #         "- Include files that appear under headings like ## `path/to/file.py` or ## path/to/file.py followed by a code fence.\n"
# # #         "- If a file appears multiple times, prefer the latest complete fence.\n"
# # #         "- Paths should be relative to the project root; do NOT prefix with './' or absolute paths.\n"
# # #         "- Use `root` if you can infer it (e.g., the top folder name). Otherwise null.\n"
# # #         "- Do NOT add commentary or extra keys."
# # #     )
# # #     usr = f"Root hint: {root_hint or '(none)'}\n\nRAW DUMP START\n{raw_text}\nRAW DUMP END"

# # #     try:
# # #         content = _chat(
# # #             provider,
# # #             [{"role": "system", "content": sys}, {"role": "user", "content": usr}],
# # #             json_mode=True,
# # #             max_tokens=8000,
# # #         )
# # #         obj = _extract_json_object(content)
# # #         if not obj or "files" not in obj or not isinstance(obj.get("files"), list):
# # #             return _fallback_structure()
# # #         cleaned = []
# # #         for it in obj["files"]:
# # #             path = (it or {}).get("path")
# # #             body = (it or {}).get("content")
# # #             if isinstance(path, str) and isinstance(body, str):
# # #                 cleaned.append({"path": path.strip(), "content": body})
# # #         return {"root": obj.get("root"), "files": cleaned}
# # #     except Exception:
# # #         return _fallback_structure()


# # # # -------- stage 2: exact file rescue (optional) -------- #

# # # def llm_extract_single_file(raw_text: str, rel_path: str, provider: str = "groq") -> str:
# # #     provider = _provider_from_env(provider)
# # #     if not _have_keys(provider):
# # #         return ""

# # #     sys = (
# # #         "You are a precise file extractor. You will be given a project dump and a relative path.\n"
# # #         "If the dump contains a code fence that corresponds to that exact file path under a heading "
# # #         "like '## path' or '## `path`', return ONLY the code inside that fence, verbatim. "
# # #         "If multiple, return the most complete/latest version. If not found, return an empty string. "
# # #         "Do NOT return JSON. Do NOT add comments. Return code only, no fences."
# # #     )
# # #     usr = f"PATH: {rel_path}\n\nRAW DUMP START\n{raw_text}\nRAW DUMP END"

# # #     try:
# # #         body = _chat(
# # #             provider,
# # #             [{"role": "system", "content": sys}, {"role": "user", "content": usr}],
# # #             json_mode=False,
# # #             max_tokens=6000,
# # #         )
# # #         s = body.strip()
# # #         if s.startswith("```") or s.startswith("~~~"):
# # #             s = s.strip("`~")
# # #             nl = s.find("\n")
# # #             if nl != -1:
# # #                 s = s[nl + 1 :]
# # #         return s
# # #     except Exception:
# # #         return ""


# # # def llm_backfill_file(path: str, hint: str = "", provider: str = "groq", *, context: Optional[str] = None) -> str:
# # #     provider = _provider_from_env(provider)
# # #     if not _have_keys(provider):
# # #         return _fallback_backfill(path)

# # #     ext = path.lower().split(".")[-1] if "." in path else ""
# # #     style = {
# # #         "py": "Production-ready Python; minimal but working; add short docstrings.",
# # #         "md": "Concise documentation.",
# # #         "yml": "Minimal, valid YAML.",
# # #         "yaml": "Minimal, valid YAML.",
# # #         "dockerfile": "Minimal Dockerfile.",
# # #         "toml": "Valid TOML.",
# # #     }.get(ext, "Appropriate content for the file.")

# # #     sys = (
# # #         "You are a senior engineer filling in a missing file for a Python/FastAPI style project. "
# # #         "Return ONLY the file body — no code fences."
# # #     )
# # #     usr = (
# # #         f"Path: {path}\nHint: {hint or '(none)'}\nStyle: {style}\n"
# # #         f"Project context (may include other files/spec):\n{context or '(no context)'}\n"
# # #         "Return only the file contents."
# # #     )

# # #     try:
# # #         body = _chat(
# # #             provider,
# # #             [{"role": "system", "content": sys}, {"role": "user", "content": usr}],
# # #             json_mode=False,
# # #             max_tokens=4000,
# # #         )
# # #         s = body.strip()
# # #         if s.startswith("```") or s.startswith("~~~"):
# # #             s = s.strip("`~")
# # #             nl = s.find("\n")
# # #             if nl != -1:
# # #                 s = s[nl + 1 :]
# # #             s = s.strip()
# # #         return s or _fallback_backfill(path)
# # #     except Exception:
# # #         return _fallback_backfill(path)
