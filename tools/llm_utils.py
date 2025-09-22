# tools/llm_utils.py
from __future__ import annotations

import os
import time
import random
import threading
from typing import Callable, TypeVar, Any, Optional

T = TypeVar("T")

# Process-wide lock + timestamp to serialize **all** outbound LLM calls
_global_lock = threading.Lock()
_last_call_ts: float = 0.0

def _now() -> float:
    return time.monotonic()

def _sleep_ms(ms: int) -> None:
    if ms > 0:
        time.sleep(ms / 1000.0)

def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.getenv(name, "").strip())
        return v if v >= 0 else default
    except Exception:
        return default

def _log(logger: Optional[Callable[[str], Any]], msg: str) -> None:
    if logger:
        logger(msg)

def llm_call_with_retry(
    fn: Callable[[], T],
    *,
    logger: Optional[Callable[[str], Any]] = None,
    tag: str = "llm",
) -> T:
    """
    Wrap an LLM call with:
      - Global serialization (no overlap)
      - Min inter-call delay
      - Retries with exponential backoff + jitter

    Tunables (Streamlit sets these via env):
      LLM_MIN_DELAY_MS      default 1200
      LLM_MAX_RETRIES       default 4
      LLM_BACKOFF_BASE_MS   default 800
      LLM_BACKOFF_MAX_MS    default 8000
    """
    min_delay_ms   = _env_int("LLM_MIN_DELAY_MS", 1200)
    max_retries    = _env_int("LLM_MAX_RETRIES", 4)
    base_backoff   = _env_int("LLM_BACKOFF_BASE_MS", 800)
    max_backoff    = _env_int("LLM_BACKOFF_MAX_MS", 8000)

    attempt = 0
    last_err: Optional[Exception] = None

    while attempt <= max_retries:
        with _global_lock:
            global _last_call_ts
            elapsed_ms = int((_now() - _last_call_ts) * 1000)
            wait_ms = max(0, min_delay_ms - elapsed_ms)
            if wait_ms > 0:
                _log(logger, f"[llm-utils] throttle {tag}: waiting {wait_ms}ms")
                _sleep_ms(wait_ms)

            try:
                result = fn()
                _last_call_ts = _now()
                return result
            except Exception as e:
                last_err = e
                attempt += 1
                msg = str(e).lower()
                retriable = any(kw in msg for kw in (
                    "429", "too many requests", "rate limit",
                    "timeout", "temporarily", "server error",
                    "bad gateway", "service unavailable", "5"
                ))
                # Be permissive once on vague 400s coming from upstream format quirks
                if attempt <= 1 and ("400" in msg or "bad request" in msg):
                    retriable = True

                if not retriable or attempt > max_retries:
                    _log(logger, f"[llm-utils] {tag}: giving up (attempt {attempt}) -> {e!r}")
                    break

                backoff_ms = min(max_backoff, int(base_backoff * (2 ** (attempt - 1))))
                jitter = random.randint(int(backoff_ms * 0.15), int(backoff_ms * 0.45))
                delay_ms = backoff_ms + jitter
                _log(logger, f"[llm-utils] retry {tag}: attempt {attempt}/{max_retries} in {delay_ms}ms (err={e})")
        _sleep_ms(delay_ms)  # sleep outside the lock

    assert last_err is not None
    raise last_err
