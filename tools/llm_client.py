from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx


# ---------------------
# Logging helpers
# ---------------------

def _log_sink() -> Optional[Path]:
    p = os.getenv("LLM_LOG_FILE", "").strip()
    if not p:
        return None
    path = Path(p)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

def _writeln(line: str) -> None:
    if os.getenv("LLM_DEBUG", "0") == "1":
        print(line, flush=True)
    dest = _log_sink()
    if dest:
        with dest.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

def _clean_env(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = s.strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1]
    return s.strip()


# ---------------------
# Client
# ---------------------

class LLMClient:
    """
    Provider-agnostic chat client (env-only) with:
      • detailed logging,
      • per-call throttle (LLM_THROTTLE_MS),
      • 429-aware sleep using Retry-After or "Please try again in Xs" hint.

    ENV (required):
      - LLM_PROVIDER = groq | openai
      - GROQ_API_KEY or OPENAI_API_KEY

    ENV (optional):
      - LLM_MODEL / GROQ_MODEL / OPENAI_MODEL
      - LLM_TEMPERATURE (default 0.0)
      - LLM_MAX_RETRIES (default 5)
      - LLM_BACKOFF_BASE_MS (default 800)
      - LLM_BACKOFF_MAX_MS  (default 8000)
      - LLM_TIMEOUT (seconds, default 120)
      - LLM_THROTTLE_MS (default 1200)       # sleep before every request
      - LLM_LOG_FILE (e.g., ./logs/llm_calls.log)
      - LLM_DEBUG=1
      - LLM_BASE_URL (advanced override)
    """

    def __init__(self) -> None:
        provider = _clean_env(os.getenv("LLM_PROVIDER")) or "groq"
        provider = provider.lower()
        self.provider = provider

        base_override = _clean_env(os.getenv("LLM_BASE_URL"))
        if provider == "groq":
            self.base_url = base_override or "https://api.groq.com/openai/v1"
            self.api_key = _clean_env(os.getenv("GROQ_API_KEY"))
            default_model = "llama-3.3-70b-versatile"
            model = _clean_env(os.getenv("LLM_MODEL")) or _clean_env(os.getenv("GROQ_MODEL")) or default_model
        elif provider == "openai":
            self.base_url = base_override or "https://api.openai.com/v1"
            self.api_key = _clean_env(os.getenv("OPENAI_API_KEY"))
            default_model = "gpt-4o-mini"
            model = _clean_env(os.getenv("LLM_MODEL")) or _clean_env(os.getenv("OPENAI_MODEL")) or default_model
        else:
            raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")

        if not self.api_key:
            missing = "GROQ_API_KEY" if provider == "groq" else "OPENAI_API_KEY"
            raise RuntimeError(f"Missing env: {missing}")

        self.model = model
        self.timeout = float(_clean_env(os.getenv("LLM_TIMEOUT")) or "120")
        self.max_retries = int(_clean_env(os.getenv("LLM_MAX_RETRIES")) or "5")
        self.backoff_base_ms = int(_clean_env(os.getenv("LLM_BACKOFF_BASE_MS")) or "800")
        self.backoff_max_ms = int(_clean_env(os.getenv("LLM_BACKOFF_MAX_MS")) or "8000")
        self.temperature = float(_clean_env(os.getenv("LLM_TEMPERATURE")) or "0.0")
        self.throttle_ms = int(_clean_env(os.getenv("LLM_THROTTLE_MS")) or "1200")

        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        _writeln(
            f"[llm:init] provider={self.provider} model={self.model} "
            f"base={self.base_url} timeout={self.timeout}s retries={self.max_retries} "
            f"backoff={self.backoff_base_ms}→{self.backoff_max_ms}ms throttle={self.throttle_ms}ms"
        )

    # ---------------- Chat APIs ----------------

    def _maybe_sleep_throttle(self) -> None:
        if self.throttle_ms > 0:
            time.sleep(self.throttle_ms / 1000.0)

    def _sleep_for_rate_limit(self, r: httpx.Response, preview: str, delay_ms: int) -> int:
        """
        Returns the next delay_ms to use after sleeping for the proper time.
        Priority:
          1) Retry-After header (seconds)
          2) "Please try again in 43.184s" hint in body
          3) exponential backoff delay_ms
        """
        # Retry-After header
        retry_after = r.headers.get("retry-after")
        if retry_after:
            try:
                secs = float(retry_after)
                secs = max(secs, delay_ms / 1000.0)
                _writeln(f"[llm:rate] obeying Retry-After={secs:.3f}s")
                time.sleep(secs)
                return min(max(int(secs * 1000 * 2), delay_ms), self.backoff_max_ms)
            except Exception:
                pass

        # Body hint: "Please try again in 43.184s"
        m = re.search(r"try again in\s+([0-9.]+)s", preview)
        if m:
            secs = float(m.group(1))
            secs = max(secs, delay_ms / 1000.0)
            _writeln(f"[llm:rate] obeying body-hint wait={secs:.3f}s")
            time.sleep(secs)
            return min(max(int(secs * 1000 * 1.5), delay_ms), self.backoff_max_ms)

        # Fallback: exponential backoff
        time.sleep(delay_ms / 1000.0)
        return min(delay_ms * 2, self.backoff_max_ms)

    def chat(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        """
        Return the raw JSON response from the provider's chat/completions API.
        Logs each attempt with status codes and short response previews.
        """
        url = f"{self.base_url}/chat/completions"
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }

        # Determine if the model is a GPT-5 variant
        is_gpt5 = self.model.lower().startswith("gpt-5")

        # Handle temperature based on model compatibility
        if is_gpt5:
            # For GPT-5 models, specifically set temperature to 1.0 as per error,
            # which is the only supported value if temperature is specified.
            payload["temperature"] = 1.0
        else:
            # For other models, use the configured temperature (default 0.0 or from env)
            payload["temperature"] = self.temperature

        # Add response_format if it's in kwargs and pop it to avoid conflicts with .update
        if "response_format" in kwargs:
            payload["response_format"] = kwargs.pop("response_format")

        payload.update(kwargs) # Add any other remaining kwargs

        # Build a safe preview of non-message kwargs for logging
        try:
            prompt_chars = sum(len(m.get("content", "")) for m in messages if isinstance(m, dict))
        except Exception:
            prompt_chars = -1
        kwargs_preview = {key: payload[key] for key in payload if key != "messages"}
        if "response_format" in kwargs_preview:
            kwargs_preview["response_format"] = str(kwargs_preview["response_format"])

        _writeln(
            f"[llm:req] POST {url} model={self.model} "
            f"prompt_chars={prompt_chars} kwargs={json.dumps(kwargs_preview, ensure_ascii=False)}"
        )

        # Per-call throttle (simple TPM guard)
        self._maybe_sleep_throttle()

        delay_ms = self.backoff_base_ms
        for attempt in range(1, self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    r = client.post(url, headers=self._headers, json=payload)

                if r.status_code == 200:
                    _writeln(f"[llm:ok] attempt={attempt} status=200 len={len(r.text)}")
                    return r.json()

                preview = r.text[:500].replace("\n", "\\n")
                _writeln(f"[llm:err] attempt={attempt} status={r.status_code} body≈{preview}")

                if r.status_code == 429:
                    delay_ms = self._sleep_for_rate_limit(r, preview, delay_ms)
                    continue
                if r.status_code in (500, 502, 503, 504):
                    time.sleep(delay_ms / 1000.0)
                    delay_ms = min(int(delay_ms * 2), self.backoff_max_ms)
                    continue

                # For client-side errors (4xx other than 429), re-raising immediately
                # is generally better than retrying with the same bad parameters.
                raise RuntimeError(f"LLM error {r.status_code}: {r.text}")

            except Exception as e:
                _writeln(f"[llm:exc] attempt={attempt} exc={type(e).__name__}: {e}")
                if attempt == self.max_retries:
                    raise
                # Only retry for specific HTTP errors (like 429, 5xx).
                # For other HTTPStatusErrors (e.g., 400), re-raise immediately.
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500 and e.response.status_code != 429:
                     raise # Do not retry 4xx client errors except 429
                time.sleep(delay_ms / 1000.0)
                delay_ms = min(int(delay_ms * 2), self.backoff_max_ms)

        raise RuntimeError("Exhausted retries")

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        response_format: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Any:
        """
        Ask for JSON mode when available and parse the result.
        """
        if response_format is None:
            response_format = {"type": "json_object"}

        resp = self.chat(messages, response_format=response_format, **kwargs)
        try:
            content = resp["choices"][0]["message"]["content"]
        except KeyError as e:
            _writeln(f"[llm:bad] missing message.content in response: {resp}")
            raise RuntimeError(f"Malformed LLM response: {resp}") from e

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
            if m:
                return json.loads(m.group(1))
            cleaned = content.strip().strip("`")
            return json.loads(cleaned)
        
        
# # tools/llm_client.py
# from __future__ import annotations

# import json
# import os
# import re
# import time
# from pathlib import Path
# from typing import Any, Dict, List, Optional

# import httpx


# # ---------------------
# # Logging helpers
# # ---------------------

# def _log_sink() -> Optional[Path]:
#     p = os.getenv("LLM_LOG_FILE", "").strip()
#     if not p:
#         return None
#     path = Path(p)
#     path.parent.mkdir(parents=True, exist_ok=True)
#     return path

# def _writeln(line: str) -> None:
#     if os.getenv("LLM_DEBUG", "0") == "1":
#         print(line, flush=True)
#     dest = _log_sink()
#     if dest:
#         with dest.open("a", encoding="utf-8") as f:
#             f.write(line + "\n")

# def _clean_env(s: Optional[str]) -> Optional[str]:
#     if s is None:
#         return None
#     s = s.strip()
#     if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
#         s = s[1:-1]
#     return s.strip()


# # ---------------------
# # Client
# # ---------------------

# class LLMClient:
#     """
#     Provider-agnostic chat client (env-only) with:
#       • detailed logging,
#       • per-call throttle (LLM_THROTTLE_MS),
#       • 429-aware sleep using Retry-After or "Please try again in Xs" hint.

#     ENV (required):
#       - LLM_PROVIDER = groq | openai
#       - GROQ_API_KEY or OPENAI_API_KEY

#     ENV (optional):
#       - LLM_MODEL / GROQ_MODEL / OPENAI_MODEL
#       - LLM_TEMPERATURE (default 0.0)
#       - LLM_MAX_RETRIES (default 5)
#       - LLM_BACKOFF_BASE_MS (default 800)
#       - LLM_BACKOFF_MAX_MS  (default 8000)
#       - LLM_TIMEOUT (seconds, default 120)
#       - LLM_THROTTLE_MS (default 1200)       # sleep before every request
#       - LLM_LOG_FILE (e.g., ./logs/llm_calls.log)
#       - LLM_DEBUG=1
#       - LLM_BASE_URL (advanced override)
#     """

#     def __init__(self) -> None:
#         provider = _clean_env(os.getenv("LLM_PROVIDER")) or "groq"
#         provider = provider.lower()
#         self.provider = provider

#         base_override = _clean_env(os.getenv("LLM_BASE_URL"))
#         if provider == "groq":
#             self.base_url = base_override or "https://api.groq.com/openai/v1"
#             self.api_key = _clean_env(os.getenv("GROQ_API_KEY"))
#             default_model = "llama-3.3-70b-versatile"
#             model = _clean_env(os.getenv("LLM_MODEL")) or _clean_env(os.getenv("GROQ_MODEL")) or default_model
#         elif provider == "openai":
#             self.base_url = base_override or "https://api.openai.com/v1"
#             self.api_key = _clean_env(os.getenv("OPENAI_API_KEY"))
#             default_model = "gpt-4o-mini"
#             model = _clean_env(os.getenv("LLM_MODEL")) or _clean_env(os.getenv("OPENAI_MODEL")) or default_model
#         else:
#             raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")

#         if not self.api_key:
#             missing = "GROQ_API_KEY" if provider == "groq" else "OPENAI_API_KEY"
#             raise RuntimeError(f"Missing env: {missing}")

#         self.model = model
#         self.timeout = float(_clean_env(os.getenv("LLM_TIMEOUT")) or "120")
#         self.max_retries = int(_clean_env(os.getenv("LLM_MAX_RETRIES")) or "5")
#         self.backoff_base_ms = int(_clean_env(os.getenv("LLM_BACKOFF_BASE_MS")) or "800")
#         self.backoff_max_ms = int(_clean_env(os.getenv("LLM_BACKOFF_MAX_MS")) or "8000")
#         self.temperature = float(_clean_env(os.getenv("LLM_TEMPERATURE")) or "0.0")
#         self.throttle_ms = int(_clean_env(os.getenv("LLM_THROTTLE_MS")) or "1200")

#         self._headers = {
#             "Authorization": f"Bearer {self.api_key}",
#             "Content-Type": "application/json",
#         }

#         _writeln(
#             f"[llm:init] provider={self.provider} model={self.model} "
#             f"base={self.base_url} timeout={self.timeout}s retries={self.max_retries} "
#             f"backoff={self.backoff_base_ms}→{self.backoff_max_ms}ms throttle={self.throttle_ms}ms"
#         )

#     # ---------------- Chat APIs ----------------

#     def _maybe_sleep_throttle(self) -> None:
#         if self.throttle_ms > 0:
#             time.sleep(self.throttle_ms / 1000.0)

#     def _sleep_for_rate_limit(self, r: httpx.Response, preview: str, delay_ms: int) -> int:
#         """
#         Returns the next delay_ms to use after sleeping for the proper time.
#         Priority:
#           1) Retry-After header (seconds)
#           2) "Please try again in 43.184s" hint in body
#           3) exponential backoff delay_ms
#         """
#         # Retry-After header
#         retry_after = r.headers.get("retry-after")
#         if retry_after:
#             try:
#                 secs = float(retry_after)
#                 secs = max(secs, delay_ms / 1000.0)
#                 _writeln(f"[llm:rate] obeying Retry-After={secs:.3f}s")
#                 time.sleep(secs)
#                 return min(max(int(secs * 1000 * 2), delay_ms), self.backoff_max_ms)
#             except Exception:
#                 pass

#         # Body hint: "Please try again in 43.184s"
#         m = re.search(r"try again in\s+([0-9.]+)s", preview)
#         if m:
#             secs = float(m.group(1))
#             secs = max(secs, delay_ms / 1000.0)
#             _writeln(f"[llm:rate] obeying body-hint wait={secs:.3f}s")
#             time.sleep(secs)
#             return min(max(int(secs * 1000 * 1.5), delay_ms), self.backoff_max_ms)

#         # Fallback: exponential backoff
#         time.sleep(delay_ms / 1000.0)
#         return min(delay_ms * 2, self.backoff_max_ms)

#     def chat(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
#         """
#         Return the raw JSON response from the provider's chat/completions API.
#         Logs each attempt with status codes and short response previews.
#         """
#         url = f"{self.base_url}/chat/completions"
#         payload: Dict[str, Any] = {
#             "model": self.model,
#             "messages": messages,
#             "temperature": self.temperature,
#         }
#         payload.update(kwargs)

#         # Build a safe preview of non-message kwargs for logging
#         try:
#             prompt_chars = sum(len(m.get("content", "")) for m in messages if isinstance(m, dict))
#         except Exception:
#             prompt_chars = -1
#         kwargs_preview = {key: payload[key] for key in payload if key != "messages"}
#         if "response_format" in kwargs_preview:
#             kwargs_preview["response_format"] = str(kwargs_preview["response_format"])

#         _writeln(
#             f"[llm:req] POST {url} model={self.model} "
#             f"prompt_chars={prompt_chars} kwargs={json.dumps(kwargs_preview, ensure_ascii=False)}"
#         )

#         # Per-call throttle (simple TPM guard)
#         self._maybe_sleep_throttle()

#         delay_ms = self.backoff_base_ms
#         for attempt in range(1, self.max_retries + 1):
#             try:
#                 with httpx.Client(timeout=self.timeout) as client:
#                     r = client.post(url, headers=self._headers, json=payload)

#                 if r.status_code == 200:
#                     _writeln(f"[llm:ok] attempt={attempt} status=200 len={len(r.text)}")
#                     return r.json()

#                 preview = r.text[:500].replace("\n", "\\n")
#                 _writeln(f"[llm:err] attempt={attempt} status={r.status_code} body≈{preview}")

#                 if r.status_code == 429:
#                     delay_ms = self._sleep_for_rate_limit(r, preview, delay_ms)
#                     continue
#                 if r.status_code in (500, 502, 503, 504):
#                     time.sleep(delay_ms / 1000.0)
#                     delay_ms = min(int(delay_ms * 2), self.backoff_max_ms)
#                     continue

#                 # Non-retryable -> raise immediately with detail
#                 raise RuntimeError(f"LLM error {r.status_code}: {r.text}")

#             except Exception as e:
#                 _writeln(f"[llm:exc] attempt={attempt} exc={type(e).__name__}: {e}")
#                 if attempt == self.max_retries:
#                     raise
#                 time.sleep(delay_ms / 1000.0)
#                 delay_ms = min(int(delay_ms * 2), self.backoff_max_ms)

#         raise RuntimeError("Exhausted retries")

#     def chat_json(
#         self,
#         messages: List[Dict[str, str]],
#         response_format: Optional[Dict[str, Any]] = None,
#         **kwargs,
#     ) -> Any:
#         """
#         Ask for JSON mode when available and parse the result.
#         """
#         if response_format is None:
#             response_format = {"type": "json_object"}

#         resp = self.chat(messages, response_format=response_format, **kwargs)
#         try:
#             content = resp["choices"][0]["message"]["content"]
#         except KeyError as e:
#             _writeln(f"[llm:bad] missing message.content in response: {resp}")
#             raise RuntimeError(f"Malformed LLM response: {resp}") from e

#         try:
#             return json.loads(content)
#         except json.JSONDecodeError:
#             m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
#             if m:
#                 return json.loads(m.group(1))
#             cleaned = content.strip().strip("`")
#             return json.loads(cleaned)
