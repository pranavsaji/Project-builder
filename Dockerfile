# ---- Project Builder (Streamlit) ----
FROM python:3.11-slim

# System deps (git for version info; build tools for any libs that need it)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git curl tini && \
    rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python deps first (better layer caching)
COPY requirements.txt ./requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy the rest of the repo
COPY . .

# Streamlit defaults (can be overridden in docker-compose)
ENV STREAMLIT_SERVER_PORT=8502 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0

# Optional: run the code-filler prestart hook (will no-op if disabled)
# These envs can be set in .env or docker-compose:
#   CODEFILL_ENABLE=1
#   CODEFILL_ON_STARTUP=1
#   CODEFILL_MODE=overwrite|skip
#   CODEFILL_CREATE_MISSING=1
#   CODEFILL_DUMP_FILE=/workspace/dump.txt
#
# We keep this in the entrypoint so the container respects runtime env.
ENTRYPOINT ["/usr/bin/tini", "--"]

# Run prestart hook if enabled, then boot Streamlit
CMD bash -lc '\
    if [ "${CODEFILL_ENABLE:-0}" = "1" ] && [ "${CODEFILL_ON_STARTUP:-0}" = "1" ]; then \
        echo "[prestart] Running tools/prestart_codefill.py"; \
        python -u tools/prestart_codefill.py || echo "[prestart] codefill failed (continuing)"; \
    else \
        echo "[prestart] Skipped (CODEFILL_ENABLE/CODEFILL_ON_STARTUP not set)"; \
    fi; \
    exec streamlit run apps/streamlit_app.py --server.port ${STREAMLIT_SERVER_PORT} --server.address ${STREAMLIT_SERVER_ADDRESS} \
'
EXPOSE 8502
